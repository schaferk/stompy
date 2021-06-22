import numpy as np
from . import nefis
from contextlib import contextmanager
import six

# utilities to grab some data from the process database.
# since the process database is specific to an installation/version,
# and scenarios already carry around paths to their installation,
# ProcessDB requires a scenario in order to find the appropriate
# database files, or explicit paths
class SubstanceDef(object):
    def __str__(self):
        return "SubstanceDef(%s)"%str(self.__dict__)
    def __repr__(self):
        return "SubstanceDef(%s)"%str(self.__dict__)

class ProcDef(object):
    def __str__(self):
        return "ProcDef(%s)"%str(self.__dict__)
    def __repr__(self):
        return "ProcDef(%s)"%str(self.__dict__)

class ProcessDB(object):
    def __init__(self,scenario=None,proc_dat=None,proc_def=None,proc=None):
        self.scenario=scenario # may be None

        if not (proc_dat and proc_def):
            if not proc:
                proc=self.scenario.proc_path
            proc_dat=proc +".dat"
            proc_def=proc +".def"
            
        self.proc_dat=proc_dat
        self.proc_def=proc_def
        
    @contextmanager
    def nef(self):
        nef=nefis.Nefis(self.proc_dat,self.proc_def)
        yield nef
        nef.close()

    def p2_idx_by_item_id(self,subst):
        return self.find_item(table='TABLE_P2',column='ITEM_ID',value=subst)

    def p4_idx_by_item_id(self,proc):
        return self.find_item(table='TABLE_P4',column='PROC_ID',value=proc)

    def find_item(self,table,column,value):
        with self.nef() as db:
            items=db[table].getelt(column,[0])

        for i,item in enumerate(items):
            # py3 - have to be careful of bytes vs. str
            if six.PY3:
                item=item.decode()
            if item.strip().lower() == value.lower():
                return i
        return None
    
    def substance_by_id(self,subst):
        idx=self.p2_idx_by_item_id(subst)
        if idx is None:
            return None

        sub=SubstanceDef()

        with self.nef() as db:
            for elt in ['ITEM_ID','ITEM_NM','UNIT','DEFAULT','AGGREGA','DISAGGR',
                        'GROUPID','SEG_EXC','WK']:
                val=db['TABLE_P2'].getelt(elt,[0,idx])
                val=val.item()
                if six.PY3 and isinstance(val,bytes):
                    val=val.decode()
                if isinstance(val,str):
                    val=val.strip()

                setattr(sub,elt.lower(),val)
        return sub

    def process_by_id(self,proc_id):
        # For substances, get a numeric index from p2_idx_by_item_id,
        # then consult TABLE_P2
        # Processes are in TABLE_P4

        # Really just want the process name and description.
        # name is something like Nitrif_NH4
        # Currently Kenny's code just leaves description blank
        idx=self.p4_idx_by_item_id(proc_id)
        if idx is None:
            return None

        proc=ProcDef()

        with self.nef() as db:
            for elt in ['PROC_ID','PROC_NAME','PROC_FORT','PROC_TRCO']:
                val=db['TABLE_P4'].getelt(elt,[0,idx])
                val=val.item()
                if six.PY3 and isinstance(val,bytes):
                    val=val.decode()
                if isinstance(val,str):
                    val=val.strip()

                setattr(proc,elt.lower(),val)
        return proc

