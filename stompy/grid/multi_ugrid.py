"""
Open ugrid-ish subdomains and approximate a global domain

Currently this is pretty slow, due to the add_grid() step.

The ghost-cell handling is also very basic, and does not
properly distinguish ghost cells along land boundaries.

TODO: handling isel for partitioned dimensions
  handling DFM output with FlowLink vs. NetLink
"""

import glob
import xarray as xr
import numpy as np
from . import unstructured_grid
import logging
log=logging.getLogger('multi_ugrid')

class MultiVar(object):
    """ 
    Proxy for a single variable of a MultiUgrid instance.
    i.e. DataArray.
    Handles isel() calls by dispatching non-partitioned dimensions
    """
    def __init__(self,mu,sub_vars):
        self.mu=mu
        self.sub_vars=sub_vars

        # sv_dims: list of dimensions of the underlying sub_vars
        #    these may change by isel() that eliminate non-partition
        #    dimensions.
        # dims: active dimensions of the MultiVar. These may change by
        #    isel() that eliminate partition dimensions. In that case
        #    the result may no longer be MultiVar.
        # Need to think about this more. Which of these should be
        # used in shape_and_indices?
        self.sv_dims=self.dims=self.sub_vars[0].dims
        self.part_dims={}
        for dim in self.dims:
            if self.mu.rev_meta.get(dim,None) in ['face_dimension','node_dimension','edge_dimension']:
                self.part_dims[dim]=slice(None)
        
    # Still possible to request
    def __repr__(self):
        return "MultiVar wrapper around %s"%repr(self.sub_vars[0])
    def __str__(self):
        return "MultiVar wrapper around %s"%str(self.sub_vars[0])
    def isel(self,**kwargs):
        # This part only works for kwargs not indexing the partitioned
        # dimensions.
        # So how *should* this work when isel is called with something
        # like cell=100 ?
        # Options:
        # - Shed the Multivar layer and create a new xr.DataArray, processing
        #   the isel. Might create performance issues.
        # - Extend Multivar to save indexing information that cannot be
        #   be applied per subvar.
        #      probably on creation of the MultiVar, would populate a list of
        #      partitioned dimensions, with a starting indexing of slice(None)
        #   An isel() call then would apply non-partitioned dimensions to the
        #    subvars, and partitioned dimensions to the local self.part_dims.
        #    How would those be stored? in the general case, would want an index
        #    array, right?
        #   self.part_dims['nFlowElem']=slice(None)
        #    Work back from how this would be used:
        #     on a call to values, the result of shape_and_indexes would be
        #     further processed to apply the part_dims.
        #    
        # - Add another wrapper class that handles indexing along partitioned
        #   dimensions.
        return MultiVar(self.mu,
                        [da.isel(**kwargs) for da in self.sub_vars])
    def sel(self,**kwargs):
        return MultiVar(self.mu,
                        [da.sel(**kwargs) for da in self.sub_vars])

    def shape_and_indexes(self):
        """
        Determine the shape of the combined variable, and how to 
        index the source datasets.
        returns ( shape, left_idx, right_idx)
        each of left_idx and right_idx are a list of functions
        each function takes subdomain index (0-based), and returns the
        corresponding entry for indexing.  left_idx takes care of the
        multi-domain merging by including an index array for dimensions
        which get merged across subdomains.
        right_idx handles ghost entries for merged dimensions, and collected
        subsetting for all dimensions.
        """
        sv0=self.sub_vars[0] # template sub variable

        shape=[]

        l2g=None

        left_idx=[]
        right_idx=[]
        
        for dim_i,dim in enumerate(self.dims):
            right=lambda proc: slice(None)
            
            if self.mu.rev_meta.get(dim,None)=='face_dimension':
                shape.append( self.mu.grid.Ncells() )
                assert l2g is None,"Can only concatenate on one parallel dimension"
                # without ghost-handling:
                # left=lambda proc: self.mu.cell_l2g[proc]
                # With ghost-handling:
                def face_left(proc):
                    c_map=self.mu.cell_l2g[proc]
                    sel=c_map>=0
                    return c_map[sel]
                def face_right(proc):
                    c_map=self.mu.cell_l2g[proc]
                    sel=c_map>=0
                    return sel
                
                left=face_left
                right=face_right
            elif self.mu.rev_meta.get(dim,None)=='edge_dimension':
                shape.append( self.mu.grid.Nedges() )
                assert l2g is None,"Can only concatenate on one parallel dimension"
                left=lambda proc: self.mu.edge_l2g[proc]
            elif self.mu.rev_meta.get(dim,None)=='node_dimension':
                shape.append( self.mu.grid.Nnodes() )
                assert l2g is None,"Can only concatenate on one parallel dimension"
                left=lambda proc: self.mu.node_l2g[proc]
            else:
                shape.append( sv0.shape[dim_i] )
                left=lambda proc: slice(None)
                
            right_idx.append( right ) # no subsetting on rhs for now.
            left_idx.append( left )
            
        return shape,left_idx,right_idx
    @property
    def shape(self):
        return self.shape_and_indices()[0]
    
    @property
    def values(self):
        """
        Combine subdomain values
        """
        shape,left_idx,right_idx=self.shape_and_indexes()
        
        sv0=self.sub_vars[0] # template sub variable
        result=np.zeros( shape, sv0.dtype)

        # Copy subdomains to global:
        
        for proc,sv in enumerate(self.sub_vars):
            # In the future may want to control which subdomain provides
            # a value in ghost cells, by having some values of the mapping
            # negative, and they get filtered out here.
            left_slice =tuple( [i(proc) for i in left_idx ])
            right_slice=tuple( [i(proc) for i in right_idx])
            result[left_slice]=sv.values[right_slice]
        return result

    def __array__(self):
        """ This lets numpy-expecting functions accept this franken-array
        """
        return self.values

    def __len__(self):
        shape,left_idx,right_idx=self.shape_and_indexes()
        return shape[0]
        
        
class MultiUgrid(object):
    """
    Given a list of netcdf files, each having a subdomain in ugrid, and
    possibly also having output data on the respective grids,
    Generate a global grid, and provide an interface approximating
    xarray.Dataset that performs the subdomain->global domain translation
    on the fly.
    """
    # HERE:
    # Need to figure these out so that at the very least
    # .values can invoked the right mappings.
    # one step better is to figure out that sometimes a variable doesn't
    # have any Multi-dimensions, and we can return a proper xr result
    # straight away.
    node_dim=None
    edge_dim=None
    cell_dim=None

    # Unclear if there is a situation where subdomains have to be merged with
    # a nonzero tolerance
    merge_tol=0.0
    
    def __init__(self,paths,cleanup_dfm=False,
                 **grid_kwargs):
        """
        paths: 
            list of paths to netcdf files
            single glob pattern
        """
        if isinstance(paths,str):
            paths=glob.glob(paths)
            # more likely to get datasets in order of processor rank
            # with a sort.
            paths.sort()
        self.paths=paths
        self.dss=self.load()
        self.grids=[unstructured_grid.UnstructuredGrid.read_ugrid(ds,**grid_kwargs) for ds in self.dss]

        # Build a mapping from dimension to ugrid role -- used by MultiVar to
        # decide how to aggregate
        meta=self.grids[0].nc_meta
        self.rev_meta={meta[k]:k for k in meta} # Reverse that

        if cleanup_dfm:
            for g in self.grids:
                unstructured_grid.cleanup_dfm_multidomains(g)
            # kludge DFM output (ver. 2021.03) has nNetElem and nFlowElem, which appear to
            # both be for the cell dimension
            for ds in self.dss:
                if ( ('nFlowElem' not in ds.dims) or
                     ('nNetElem' not in ds.dims)):
                    break
                if ds.dims['nFlowElem']!=ds.dims['nNetElem']:
                    log.warning("Expected dimensions nFlowElem and nNetElem to be duplicates, but %d!=%d"%
                                (ds.dims['nFlowElem'],ds.dims['nNetElem']))
                    break
            else:
                self.rev_meta['nFlowElem']='face_dimension'

        self.create_global_grid_and_mapping()
        
    def load(self):
        return [xr.open_dataset(p) for p in self.paths]
    
    def reload(self):
        """
        Close and reopen individual datasets, in case unlimited dimensions (i.e. time) have
        been extended.  Does not recompute the grid.
        """
        [ds.close() for ds in self.dss]
        self.dss=self.load()

    def create_global_grid_and_mapping(self):
        self.node_l2g=[]
        self.edge_l2g=[]
        self.cell_l2g=[]

        # initialize 
        for gnum,g in enumerate(self.grids):
            # ghost cells:
            e2c=g.edge_to_cells()
            bnd_edge=np.nonzero( e2c.min(axis=1) < 0)[0]
            bnd_cell=e2c[bnd_edge,:].max(axis=1)
            # boundary and potential ghost cells get -1, else 0.
            # not quite there, since there are boundary cells that
            # are ghost and non-ghost. better to have a count of
            # neighbors for each cell?
            # Revisit.  For now, ghostness is 0 for unset, and more
            # positive the more likely cell is to be real
            ghostness=100*np.ones(g.Ncells(), np.int32)
            ghostness[bnd_cell] -= 1
            
            if gnum==0:
                self.grid=self.grids[0].copy()
                n_map=np.arange(self.grid.Nnodes())
                j_map=np.arange(self.grid.Nedges())
                c_map=np.arange(self.grid.Ncells())
                self.grid.add_cell_field( 'ghostness', ghostness )
                self.grid.add_cell_field( 'proc', np.zeros( g.Ncells(), np.int32) )
            else:
                n_map,j_map,c_map = self.grid.add_grid(g,merge_nodes='auto',
                                                       tol=self.merge_tol)
                # c_map will be g.Ncells(), mapping to global idx.
                # either ghostness not set, or the existing value is ghostier
                # than new value:
                sel_proc=self.grid.cells['ghostness'][c_map] < ghostness
                c_map=np.where(sel_proc, c_map, -1)
                # just the selected cells get this proc
                self.grid.cells['proc'][c_map[sel_proc]]=gnum
                self.grid.cells['ghostness'][c_map[sel_proc]]=ghostness[sel_proc]

            self.node_l2g.append(n_map)
            self.edge_l2g.append(j_map)
            self.cell_l2g.append(c_map)
            
    def build_cell_g2l(self):
        cell_g2l=np.zeros((self.grid.Ncells(),2),np.int32)
        for proc,l2g in enumerate(self.cell_l2g):
            valid=l2g>=0
            cell_g2l[l2g[valid],0]=proc
            cell_g2l[l2g[valid],1]=np.arange(len(l2g))[valid]
        self.cell_g2l=cell_g2l
    
    def __getitem__(self,k):
        # return a proxy object - can't do the translation until .values is called.
        if k in list(self.dss[0].variables.keys()):
            return MultiVar(self,
                            [ds[k] for ds in self.dss])
        else:
            raise KeyError("%s is not an existing variable"%k)
    
    def __getattr__(self,k):
        # Two broad cases here
        #  attempting to get a variable
        #     => delegate to getitem
        #  attempting some operation, that we probably don't know how to complete
        try: 
            return self.__getitem__(k)
        except KeyError:
            raise Exception("%s is not an existing variable or known method"%k)

    def __str__(self):
        return "MultiFile Layer on top of %s"%str(self.dss[0])
    def __repr__(self):
        return str(self)

    @property
    def dims(self):
        return self.dss[0].dims

    def isel(self,**kwargs):
        subset=copy.copy(self)
        subset.dss=[ds.isel(**kwargs) for ds in self.dss]
        return subset
    def sel(self,**kwargs):
        subset=copy.copy(self)
        subset.dss=[ds.sel(**kwargs) for ds in self.dss]
        return subset

