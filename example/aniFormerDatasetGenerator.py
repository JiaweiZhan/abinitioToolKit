# only support qbox yet
from abinitioToolKit import qbox_io
from abinitioToolKit import utils
from mpi4py import MPI
import argparse
from functools import partial
import signal
import pickle
from tqdm import tqdm
import numpy as np
import shutil, os, yaml
import pickle, h5py

comm = MPI.COMM_WORLD
bohr2angstrom = 0.529177249

PERIODIC_TABLE = """
    H                                                                                                                           He
    Li  Be                                                                                                  B   C   N   O   F   Ne
    Na  Mg                                                                                                  Al  Si  P   S   Cl  Ar
    K   Ca  Sc                                                          Ti  V   Cr  Mn  Fe  Co  Ni  Cu  Zn  Ga  Ge  As  Se  Br  Kr
    Rb  Sr  Y                                                           Zr  Nb  Mo  Tc  Ru  Rh  Pd  Ag  Cd  In  Sn  Sb  Te  I   Xe
    Cs  Ba  La  Ce  Pr  Nd  Pm  Sm  Eu  Gd  Tb  Dy  Ho  Er  Tm  Yb  Lu  Hf  Ta  W   Re  Os  Ir  Pt  Au  Hg  Tl  Pb  Bi  Po  At  Rn
    Fr  Ra  Ac  Th  Pa  U   Np  Pu  Am  Cm  Bk  Cf  Es  Fm  Md  No  Lr  Rf  Db  Sg  Bh  Hs  Mt  Ds  Rg  Cn  Nh  Fl  Mc  Lv  Ts  Og
    """.strip().split()

if __name__ == "__main__":
    # signal.signal(signal.SIGINT, partial(utils.handler, comm))

    rank = comm.Get_rank()
    size = comm.Get_size()
    if rank == 0:
        utils.time_now()

    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--saveFileFolder", type=str,
            help="folder that store XML sample and qbox.out. Default: ../")
    parser.add_argument("-a", "--alphaFile", type=str,
            help="Local Dielectric Function File. Default: ../alpha.txt")
    parser.add_argument("-n", "--material_name", type=str,
            help="material_name. Default: sihwat")
    parser.add_argument("-o", "--species_order", nargs='*',
            help="species_order. Default: H O Si")
    args = parser.parse_args()

    if not args.saveFileFolder:
        args.saveFileFolder = "../" 
    if not args.alphaFile:
        args.alphaFile = "../alpha.txt" 
    if not args.material_name:
        args.material_name = "sihwat" 
    if not args.species_order:
        args.species_order =  ['H', 'O', 'Si']
    material_name = args.material_name

    conf_tab = {"saveFileFolder": args.saveFileFolder,
                "alphaFile": args.alphaFile,
                "material_name": args.material_name,
                "species_order": args.species_order,
                "MPI size": comm.Get_size()}
    utils.print_conf(conf_tab)

    # ------------------------------------------- read and store wfc --------------------------------------------
    
    qbox = qbox_io.QBOXRead(comm=comm)
    storeFolder = './wfc/'

    comm.Barrier()
    isExist = os.path.exists(storeFolder)
    if not isExist:
        if rank == 0:
            print(f"store wfc from {storeFolder}")
        qbox.read(args.saveFileFolder, storeFolder=storeFolder)
    else:
        if rank == 0:
            print(f"read stored wfc from {storeFolder}")
     
    # --------------------------------------- generate training data for aniformer ----------------------------------------
    
    # comm.Barrier()
    with open(storeFolder + '/info.pickle', 'rb') as handle:
        info_data = pickle.load(handle)

    npv = info_data['npv']
    cell = info_data['cell'] * bohr2angstrom
    species_loc = info_data['atompos']

    name2index = {s: k for k, s, in enumerate(PERIODIC_TABLE, 1)}
    species, positions = [], []
    for i in species_loc:
        species.append(name2index[i[0]])
        positions.append(i[1:])
    positions = (np.array(positions) * bohr2angstrom).tolist()

    alphaFile = args.alphaFile 
    alpha = utils.read_alpha(alphaFile=alphaFile, npv=npv)

    dataset_folder = './Dataset'
    structure_folder = 'structures'
    attribute_folder = 'attributes'
    if rank == 0:
        if not os.path.exists(dataset_folder):
            os.mkdir(dataset_folder)
        if not os.path.exists(os.path.join(dataset_folder, structure_folder)):
            os.mkdir(os.path.join(dataset_folder, structure_folder))
        if not os.path.exists(os.path.join(dataset_folder, attribute_folder)):
            os.mkdir(os.path.join(dataset_folder, attribute_folder))
        if not os.path.exists(os.path.join(dataset_folder, attribute_folder, material_name)):
            os.mkdir(os.path.join(dataset_folder, attribute_folder, material_name))

    comm.Barrier()

    if rank == 0:
        structure_data = {
                'cell': cell.tolist(),
                'pbc': [True, True, True],
                'atomic_positions': positions,
                'species': species,
                'species_order': args.species_order,
                }

        point_gap = 5
        divisions = np.array([1 / npv[0], 1 / npv[1], 1 / npv[2]], dtype=float)
        i, j, k = np.mgrid[0:npv[0]:point_gap, 0:npv[1]:point_gap, 0:npv[2]:point_gap]
        point_indices = np.array([i, j, k]).T.reshape(-1, 3)
        target_point = point_indices * divisions[None, :]
        structure_data['target_point'] = target_point.tolist()

        structure_fname = f'sihwat.pkl'
        with open(os.path.join(dataset_folder, structure_folder, structure_fname), 'wb') as pickle_file:
            pickle.dump(structure_data, pickle_file)
        point_indices = point_indices.tolist()
    else:
        point_indices = None
    point_indices = comm.bcast(point_indices, root=0)
    point_indices = np.array(point_indices)

    if rank == 0:
        pbar = tqdm(desc=f'store attributes: ', total=point_indices.shape[0])
    comm.Barrier()

    i_indices, j_indices, k_indices = point_indices[:, 0], point_indices[:, 1], point_indices[:, 2]
    attributes = alpha[i_indices, j_indices, k_indices]

    for index in range(attributes.shape[0]):
        if index % size == rank:
            attribute_fname = os.path.join(dataset_folder, attribute_folder, material_name, f"{material_name}__{index + 1}")
            np.save(attribute_fname, attributes[index])

            if rank == 0:
                value = size
                if  attributes.shape[0] - index < value:
                    value = attributes.shape[0] - index
                pbar.update(value)

    comm.Barrier()
    if rank == 0:
        pbar.close()
