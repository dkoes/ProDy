# -*- coding: utf-8 -*-
"""This module defines functions for parsing MMTF files and structure objects.

.. MMTF files: https://mmtf.rcsb.org"""

from numbers import Number
import os.path
from collections import OrderedDict

from prody.atomic import AtomGroup
from prody.atomic import flags
from prody.atomic import ATOMIC_FIELDS

from prody.utilities import openFile, isListLike, copy
from prody import LOGGER
from prody.utilities.misctools import getMasses
from .header import getHeaderDict, buildBiomolecules

import struct as st
import numpy as np

try:
    from mmtf import fetch, parse
    import mmtf
except ImportError:
    print("Install mmtf to read in mmtf structure objects (e.g. pip install mmtf-python)")

__all__ = ['parseMMTF']
           
def parseMMTF(mmtf_struc, **kwargs):

    chain = kwargs.pop('chain', None)
    title = kwargs.get('title', None)

    if type(mmtf_struc)==str:
        if not mmtf_struc.endswith(".mmtf") and not mmtf_struc.endswith(".mmtf.gz"): #entering just the pdb id
            if len(mmtf_struc) == 5 and mmtf_struc.isalnum():
                if chain is None:
                    chain = mmtf_struc[-1]
                    mmtf_struc = mmtf_struc[:4]
                else:
                    raise ValueError('Please provide chain as a keyword argument '
                                    'or part of the PDB ID, not both')
            else:
                chain = chain

            if len(mmtf_struc) == 4 and mmtf_struc.isalnum():
                if title is None:
                    title = mmtf_struc
                    kwargs['title'] = title
                structure = fetch(mmtf_struc)
                if structure is None:
                    raise IOError('mmtf file for {0} could not be downloaded.'
                                    .format(mmtf_struc))
                mmtf_struc = structure
            else:
                raise IOError('{0} is not a valid filename or a valid PDB '
                            'identifier.'.format(mmtf_struc))
            
        else: #entering the mmtf file name
            structure=parse(mmtf_struc)
            if structure is None:
                raise IOError('mmtf file for {0} could not be downloaded.'
                                .format(mmtf_struc))
            mmtf_struc = structure
            title = mmtf_struc.structure_id  

    #if none of the above loops are entered, user should have passed a mmtf structure object

    if title is None:
        title = mmtf_struc.structure_id

    result = _parseMMTF(mmtf_struc, chain=chain, **kwargs)

    return result

def _parseMMTF(mmtf_struc, **kwargs):
    LOGGER.timeit()
    ag = AtomGroup()
    model = kwargs.get('model')
    subset = kwargs.get('subset')
    chain = kwargs.get('chain')
    header = kwargs.get('header', False)
    get_bonds = kwargs.get('bonds',False) 
    altloc_sel = kwargs.get('altloc', 'A')
    
    assert isinstance(header, bool), 'header must be a boolean'

    if model is not None:
        if isinstance(model, int):
            if model < 0:
                raise ValueError('model must be greater than 0')
        else:
            raise TypeError('model must be an integer, {0} is invalid'
                            .format(str(model)))
    title_suffix = ''

    biomol = kwargs.get('biomol', False)
    hd = set_header(mmtf_struc)
    ag = set_info(ag, mmtf_struc, get_bonds, altloc_sel)

    if ag.numAtoms() > 0:
            LOGGER.report('{0} atoms and {1} coordinate set(s) were '
                          'parsed in %.2fs.'.format(ag.numAtoms(),
                                                    ag.numCoordsets()))
    else:
        ag = None
        LOGGER.warn('Atomic data could not be parsed, please '
                    'check the input file.')
        
    
        
    if ag is not None and isinstance(hd, dict):
        if biomol:
            ag = buildBiomolecules(hd, ag)
            if isinstance(ag, list):
                LOGGER.info('Biomolecular transformations were applied, {0} '
                            'biomolecule(s) are returned.'.format(len(ag)))
            else:
                LOGGER.info('Biomolecular transformations were applied to the '
                            'coordinate data.')    

    if header:
        return ag, hd
    else:
        return ag


def set_header(data_api):

    #get the transform here and convert it to the format that prody wants
    assembly = data_api.bio_assembly[0]
    chain_list = data_api.chain_name_list
    assembly = bio_transform(assembly, chain_list)
    
    header = {
    'r_work': data_api.r_work,
    'r_free': data_api.r_free,
    'resolution': data_api.resolution,
    'title': data_api.title,
    'deposition_date': data_api.deposition_date,
    'release_date': data_api.release_date,
    'experimental_methods': data_api.experimental_methods,
    'biomoltrans': assembly
    }

    return header

def bio_transform(input_data, chain_list):

    output_dict = {}
    index = input_data['name']
    transforms=[]

    for item in input_data['transformList']:
        chains = list(set([chain_list[i] for i in item['chainIndexList']]))[::-1]
        transforms.append(chains)
        for i in range(0, len(item['matrix'])-4, 4):
            string_slice = item['matrix'][i:i+4]
            formatted_string = ' '.join(map(str, string_slice))
            transforms.append(formatted_string)

    output_dict[index]=transforms
      
    return output_dict

def set_info(atomgroup, mmtf_data,get_bonds=False,altloc_sel='A'):

    mmtfHETATMtypes = set([
        "D-SACCHARIDE",
        "D-SACCHARIDE 1,4 AND 1,4 LINKING",
        "D-SACCHARIDE 1,4 AND 1,6 LINKING",
        "L-SACCHARIDE",
        "L-SACCHARIDE 1,4 AND 1,4 LINKING",
        "L-SACCHARIDE 1,4 AND 1,6 LINKING",
        "NON-POLYMER",
        "OTHER",
        "PEPTIDE-LIKE",
        "SACCHARIDE"])

    asize = mmtf_data.num_atoms

    if len(mmtf_data.chains_per_model) > 1:
        # get number of atoms in first model
        asize = 0
        groupIndex = 0
        modelChainCount = mmtf_data.chains_per_model[0]
        for chainGroupCount in mmtf_data.groups_per_chain[:modelChainCount]:
            #traverse groups
            for _ in range(chainGroupCount):
                group = mmtf_data.group_list[mmtf_data.group_type_list[groupIndex]]        
                asize += len(group['atomNameList'])
                groupIndex += 1

    fields = OrderedDict()

    x = mmtf_data.x_coord_list
    y = mmtf_data.y_coord_list
    z = mmtf_data.z_coord_list

    if len(x) != mmtf_data.num_models*asize:
        LOGGER.warn('Multi-model MMTF files with different molecules not supported.  Keeping only first model')
        coords = np.array([x, y, z]).T[:asize].reshape(1, asize, 3)
    else:     
        coords = np.array([x, y, z]).T.reshape(mmtf_data.num_models, asize, 3)

    # Initialize arrays for atom properties
    atom_names = np.empty(asize, dtype=ATOMIC_FIELDS['name'].dtype)
    resnames = np.empty(asize, dtype=ATOMIC_FIELDS['resname'].dtype)
    resnums = np.empty(asize, dtype=ATOMIC_FIELDS['resnum'].dtype)
    chainids = np.empty(asize, dtype=ATOMIC_FIELDS['chain'].dtype)
    segnames = np.empty(asize, dtype=ATOMIC_FIELDS['segment'].dtype)
    hetero = np.zeros(asize, dtype=bool)
    termini = np.zeros(asize, dtype=bool)
    altlocs = np.empty(asize, dtype=ATOMIC_FIELDS['altloc'].dtype)
    icodes = np.empty(asize, dtype=ATOMIC_FIELDS['icode'].dtype)
    serials = np.empty(asize, dtype=ATOMIC_FIELDS['serial'].dtype)
    elements = np.empty(asize, dtype=ATOMIC_FIELDS['element'].dtype)
    bfactors = np.empty(asize, dtype=ATOMIC_FIELDS['beta'].dtype)
    occupancies = np.empty(asize, dtype=ATOMIC_FIELDS['occupancy'].dtype)

    modelIndex = 0
    chainIndex = 0
    groupIndex = 0
    atomIndex = 0

    #traverse models
    for modelChainCount in mmtf_data.chains_per_model:
        chain_name_list = mmtf_data.chain_name_list

        #traverse chains
        for k in range(modelChainCount):
            chainGroupCount = mmtf_data.groups_per_chain[chainIndex]

            #traverse groups
            for _ in range(chainGroupCount):
                group = mmtf_data.group_list[mmtf_data.group_type_list[groupIndex]]
                groupAtomCount = len(group.get('atomNameList'))

                #traverse atoms
                for i in range(groupAtomCount):
                    bfactors[atomIndex] = mmtf_data.b_factor_list[atomIndex]
                    altlocs[atomIndex] = mmtf_data.alt_loc_list[atomIndex]
                    occupancies[atomIndex] = mmtf_data.occupancy_list[atomIndex]
                    atom_names[atomIndex] = group.get('atomNameList')[i]
                    elements[atomIndex] = group.get('elementList')[i]
                    resnames[atomIndex] = group.get('groupName')
                    resnums[atomIndex] = mmtf_data.group_id_list[groupIndex]
                    icodes[atomIndex] = mmtf_data.ins_code_list[groupIndex]
                    chainids[atomIndex] = chain_name_list[k]
                    if group.get('chemCompType') in mmtfHETATMtypes:
                        hetero[atomIndex] = True
                    serials[atomIndex] = atomIndex + 1
                    segnames[atomIndex]=''

                    atomIndex += 1

                groupIndex += 1
            chainIndex += 1
        modelIndex += 1
        break

    #detect termini based on chain changes
    termini[:-1] = chainids[1:] != chainids[:-1]
    termini[-1] = True  #the last atom is always a terminus

    mask = np.full(asize, True, dtype=bool)
    if altloc_sel != 'all':
        #mask out any unwanted alternative locations
        mask = (altlocs == '') | (altlocs == altloc_sel)
        
    atomgroup.setCoords(coords[:,mask])
    atomgroup.setNames(atom_names[mask])
    atomgroup.setResnums(resnums[mask])
    atomgroup.setResnames(resnames[mask])
    atomgroup.setChids(chainids[mask])
    atomgroup.setElements(elements[mask])
    atomgroup.setMasses(getMasses(elements[mask]))
    atomgroup.setBetas(bfactors[mask])
    atomgroup.setAltlocs(altlocs[mask])
    atomgroup.setOccupancies(occupancies[mask])
    atomgroup.setFlags('hetatm', hetero[mask])
    atomgroup.setFlags('pdbter', termini[mask])
    atomgroup.setFlags('selpdbter', termini[mask])
    atomgroup.setSerials(serials[mask])
    atomgroup.setIcodes(icodes[mask])
    atomgroup.setSegnames(segnames[mask])
    atomgroup.setTitle(mmtf_data.structure_id)
    
    if get_bonds and hasattr(mmtf_data,'bond_atom_list'):
        #have to remap any masked out atoms
        remaining = np.arange(asize)[mask]
        remap = np.full(asize,-1)
        remap[remaining] = np.arange(len(remaining))
        allbonds = np.array(mmtf_data.bond_atom_list).reshape(-1,2)
        nonpeptide = []
        #irgnore bonds between C and N in adjacent residues
        for a,b in allbonds:
            if a < asize and b < asize and mask[a] and mask[b] :
                if atom_names[a] != 'N' or atom_names[b] != 'C' or resnums[a]-resnums[b] != 1:
                    nonpeptide.append((remap[a],remap[b]))
        atomgroup.setBonds(nonpeptide)

    return atomgroup
