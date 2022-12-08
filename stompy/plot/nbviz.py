# -*- coding: utf-8 -*-
"""
Created on Thu Aug  4 05:00:18 2022

@author: rusty
"""
import os
import time
import matplotlib.pyplot as plt
import numpy as np
from . import plot_utils
from .. import utils
from ..grid import unstructured_grid, multi_ugrid

from ipywidgets import Button, Layout, jslink, IntText, IntSlider, AppLayout, Output
import traitlets as tl
import ipywidgets as widgets

# NBViz: top level. holds a list of datasets, list of layers
#    manages the top level widget layout.
# Layer is a combination of a dataset, a variable or expression name,
#    and a plotter.  
# Dataset: roughly a netcdf file. 
#   has a list of meshes, in the VisIt sense. 
#   has a list of variables. Each variable has dimensions.
# Not yet clear on how dimensions and sliders should be managed.
#  Would like to support a default where dimensions are manipulated
#  at the dataset level, shared across layers based on that dataset.
#  But flexible enough to share a dimension slider across datasets, 
#  or have dimension sliders specific to a layer.
# Maybe this can use the observe methods?
# Probably best to separate Layer, which is more like plot-type, from
# PlotVar. It's really plotvar that needs to know dimensions.

# TODO:
#  Allow passing a grid into the NBViz constructor for a UGDataset.
#  Work around not having nc_meta. Maybe a local implementation with heuristics?
#  Add in the logic for extra sliders, specifically layer.

class Fig:
    """ Manages a single matplotlib figure
    """
    num=None
    figsize=(9.25,7)
    def __init__(self,**kw):
        utils.set_keywords(self,kw)        
        self.fig,self.ax=plt.subplots(num=self.num,clear=1,figsize=self.figsize)
        self.ax.set_adjustable('datalim')

        self.caxs=[]
                   
    def get_cax(self):
        # Colorbars. This will need to be more dynamic, with some way for
        # plots to request colorbar axis, and dispose of it later.      
        n_cax=len(self.caxs)
        cax=self.fig.add_axes([0.9,0.1+0.3*n_cax,0.02,0.25])
        self.caxs.append(cax)
        return cax
    def redraw(self):
        self.fig.canvas.draw()        
    def save_frame(self,fn):
        fn=os.path.abspath(fn)
        print("Saving to %s"%fn)
        self.fig.savefig(fn)
    def do_tight_layout(self):
        self.fig.tight_layout()

class Dataset:
    """
    Generic unstructured_grid / xarray plotting
    for jupyter notebook
    """
    def __init__(self,**kw):
        utils.set_keywords(self,kw)
        
    def add_layer(self,variable):
        raise Exception("overload")        

class UGDataset(Dataset):
    # ccoll=None
    # cell_var=None
    # ecoll=None
    # edge_var=None
    # ncoll=None
    # node_var=None
    
    def __init__(self,ds,grid=None,**kw):
        super().__init__(**kw)
        self.ds=ds
        if isinstance(ds,multi_ugrid.MultiUgrid):
            grid=ds.grid
        if grid is None:
            grid=unstructured_grid.UnstructuredGrid.read_ugrid(ds)            
        self.grid=grid
        
        # select field to plot for cells:
        self.dim_selectors=dict(time=self.ds.dims['time']-5)
        #self.set_cell_var('eta')
        
    def available_vars(self):
        # To show when creating a new plot
        return self.available_cell_vars() # + edge_vars ...

    def create_layer(self,variable):
        if variable in self.available_cell_vars():
            return UGCellLayer(self,variable)
        else:
            print("Not sure how to handle variable for layer: ",variable)
            return None

    def available_cell_vars(self):
        meta=self.grid.nc_meta
        cell_vars=[]
        for v in self.ds:
            if meta['face_dimension'] in self.ds[v].dims:
                cell_vars.append(v)
        # need more generic expression handling.
        if ('eta' in cell_vars) and ('bed_elev' in cell_vars):
            cell_vars.append('depth')
        # also should filter out variables that are known to be
        # part of the grid definition.
        return cell_vars
                
    def select_cell_data(self,variable,dims):
        if variable == 'depth': # Will get generalized to Expression
            return self.select_data('eta',dims) - self.select_data('bed_elev',dims)
        return self.select_data(variable,dims)
    def var_dims(self,v):
        if v=='depth':
            return self.ds['eta'].dims
        else:
            return self.ds[v].dims
    def select_data(self,varname,dims):
        v=self.ds[varname]
        isels={k:dims[k]
               for k in dims
               if k in v.dims}
        if len(isels):
            v=v.isel(**isels)
        return v
    
    # def update_dim_sliders(self):
    #     # all dimensions that might need to be indexed:
    #     active_dims=[]
    #     for v in [self.cell_var]: # edge var, node var
    #         if v is None: continue
    #         for d in self.var_dims(v):
    #             meta=self.grid.nc_meta
    #             if d in [meta['face_dimension'],
    #                      meta['edge_dimension'],
    #                      meta['node_dimension']]:
    #                 continue
    #             if d in active_dims: continue
    #             active_dims.append(d)
                
    #     # HERE: update to manage slider widgets
    #     #if len(active_dims)>len(self.dim_axs):
    #     #    print("More dimensions than sliders...")
    #     # self.widgets=[]
    #     # for dim_ax,dim in zip(self.dim_axs,active_dims):
    #     #     dim_ax.cla()
    #     #     widget=mw.Slider(dim_ax,dim,valmin=0,
    #     #                      valmax=self.ds.dims[dim]-1,
    #     #                      valstep=1.0)
    #     #     if dim in self.dim_selectors:
    #     #         widget.set_val(self.dim_selectors[dim])
    #     #     widget.on_changed( lambda val: self.update_dim(dim,val) )
    #     #     self.widgets.append(widget)
    #     # for k in list(self.dim_selectors):
    #     #     if k not in active_dims:
    #     #         del self.dim_selectors[k]
        

class Layer(tl.HasTraits):
    # dimensions that this layer would accept from global dimensions.
    # maps dimension name (only guaranteed unique to this layer), to xr.DataArray
    # coordinate. 
    free_dims = tl.Dict()
    @property
    def label(self):
        return str(id(self))
    
    def layer_edit_pane(self):
        return widgets.Label("Edit layer")

class UGLayer(Layer):
    def __init__(self,ds,variable):
        self.ds=ds
        self.variable=variable
        self.global_dims=None # uninitialized.
        self.local_dims=None # uninitialized.
    @property
    def label(self):
        return self.variable
    
class UGCellLayer(UGLayer):
    """
    pseudocolor plot of cell-centered values for unstructured grid
    dataset.
    """
    ccoll=None
    update_clims=True # autoscale colorbar when coordinates change

    def __init__(self,*a,**k):
        super().__init__(*a,**k)
        # local_dims will have all of the dimensions that have to be constrained
        # in order for the plot to render. when global dim updates come in, the layer
        # can choose to let those override local_dims.

        self.init_local_dims()
        
    def init_local_dims(self,defaults={}):
        self.local_dims={} # NB: possible that defaults==self.local_dims.
        ds=self.ds.ds # get the actual xarray dataset
        grid=self.ds.grid
        for dim in ds[self.variable].dims:
            if dim!=grid.nc_meta['face_dimension']:
                # use a default dim if given, otherwise punt with 0.
                self.local_dims[dim]=defaults.get(dim,0)
        # Update the advertised list of dimensions
        self.update_free_dims()

    def update_free_dims(self):
        changed=False
        for dim in self.local_dims:
            if dim not in self.free_dims:
                self.free_dims[dim] = self.ds.ds[dim] # can we just publish these as xr data arrays?
                changed=True
        for dim in list(self.free_dims):
            if dim not in self.local_dims:
                del self.free_dims[dim]
                changed=True
        #if changed:
        #    print("UGCellLayer: free dims changed")
        
    def init_plot(self,Fig):
        # okay if it is an empty dict, just not None
        assert self.local_dims is not None
        
        self.Fig=Fig
        #self.ccoll=self.ds.grid.plot_cells(values=self.get_data(),
        #                                   cmap='turbo',ax=self.Fig.ax)
        data=self.get_data()
        self.ccoll=self.ds.grid.plot_cells(values=np.ones_like(data),
                                           cmap='turbo',ax=self.Fig.ax)
        self.ccoll.set_array(data) # does this help?
        
        self.cax=self.Fig.get_cax()
        self.ccbar=plot_utils.cbar(self.ccoll,cax=self.cax)
        self.Fig.ax.axis('equal') # somehow lost the auto zooming. try here.
        
    def get_data(self):
        return self.ds.select_cell_data(self.variable,self.local_dims)
    
    def update_global_dims(self,dims):
        # This part needs some help. signalling
        # that a global dimension has changed.
        # It's up to the layer to decide whether it cares, handle local dim changes, etc.
        
        # dims: dim => index
        # If any of the dims that control this layer have changed,
        # fetch new data and update the plot.
        update_plot=False
        self.global_dims=dims # may not need to keep this
        for k in self.local_dims:
            if k in dims and self.local_dims[k]!=dims[k]:
                update_plot=True
                self.local_dims[k]=dims[k]
        if update_plot:
            self.update_arrays()
            self.redraw() # maybe too proactive
    def redraw(self):
        if self.Fig is not None and self.ccoll is not None:
            self.Fig.redraw()
    def update_arrays(self):
        # Would like to be smarter -- have callers figure out who
        # should be updated, and this just becomes the final draw 
        # call. probably keep a list of layers, broadcast dim
        # changes, and let layers notify of update.
        if self.ccoll is not None:
            data=self.get_data()
            self.ccoll.set_array(data)
            if self.update_clims:
                self.ccoll.set_clim(np.nanmin(data), np.nanmax(data))
    
    def available_vars(self):
        # to show options for changing the variable of an existing layer
        # will need to also be smarter about updating self.dims, in case
        # new variable has different dimensions. 
        return self.ds.available_cell_vars()
    
    def layer_edit_pane(self):
        var_selector = widgets.Dropdown(options=self.available_vars(),
                                        value=self.variable,
                                        description='Var:')
        var_selector.observe(self.on_change_var, names='value')
        return var_selector

    def on_change_var(self,change):
        self.set_variable(change['new'])
        
    def set_variable(self,v):
        if v==self.variable: return
        print("set_variable: ",v)
        self.variable=v

        # May need to adjust dimensions
        self.init_local_dims(defaults=self.local_dims)
        
        # This feels a bit too early. Would be nice to be more JIT.
        if self.ccoll is not None:
            data=self.get_data()
            self.ccoll.norm.autoscale(data)
            self.ccoll.set_array(data)
            self.Fig.redraw()


# use while debugging to see how layouts are working
def create_expanded_button(description, button_style):
    return Button(description=description, button_style=button_style, 
                  layout=Layout(height='auto', width='auto'))

class NBViz(widgets.AppLayout):
    """
    Manage databases, layers, and a plot window.
    This is roughly the top level, but unlike visit there is
    only one window/figure in which results are shown. 
    - May allow that one figure to have multiple axes, so keep
      ax separate
    """
    # Will try just being a subclass of the top-level
    # layout. That will streamline display logic, but
    # not sure what the other implications are.
    active_layer=None
    new_layer="--new--"
    
    def __init__(self,datasets=None):
        # at the moment datasets can only be ugrid-ish xr.Dataset,
        # to be wrapped in UGDataset
        if datasets is not None:
            self.datasets=[self.data_to_Dataset(d) for d in datasets]
        else:
            self.datasets=[]
        assert len(self.datasets)==1,"Not ready for 0 or multiple datasets"
        self.layers=[]
        self.Fig=Fig()
        # May need a display call here to get the figure to show up?
        
        # this will need to get smarter
        # dynamically find out which dimensions are active, 
        # allow for dimensions to be shared or not shared between
        # datasets and layers within datasets.
        # show sliders for active dimensions.
        # and dynamically update min/max for dimensions.
        # For now, this is implicitly just globally shared dimensions, and is hardwired
        # just to time. I think the goal is for each layer to (a) figure out what dimensions
        # it needs, (b) select those for itself in its layer_edit_pane(), and (c) opt in
        # to have that dimension driven by a global dimension.
        # so this would be better named active_global_dims
        self.active_dims={'time':0} # might need to be secondary to global_dims now.
        # But I'm using global_dims to track the coordinates
        self.global_dims={}
        
        # create widgets and call super().__init__
        self.init_layout()

    def data_to_Dataset(self,d):
        " wrap original data of some form in an appropriate NBViz dataset"
        return UGDataset(d)
    
    def init_layout(self):
        ds=self.datasets[0] # DEV

        self.coordinate_pane=self.create_coordinate_pane()
        
        self.layer_selector = widgets.Select(options=[self.new_layer],
                                             description="Layer:")
        self.layer_selector.observe(self.on_layer_change,names='value')
        
        global_controls=self.global_controls_widget()
        
        # so far have failed to use MPL figure as a widget.
        super().__init__(header=self.coordinate_pane,
                         left_sidebar=self.layer_selector,
                         center=self.new_layer_pane(), # create_expanded_button('Center', 'warning'),
                         right_sidebar=global_controls,
                         pane_widths=[5, 5, 5],
                         footer=None)
        # displaying the viz object in the notebook then displays the 
        # widgets.

    def create_coordinate_pane(self):
        """
        Also update active_dims
        """
        coord_widgets=[widgets.Label(f'Coordinates')]

        def on_coord_change(change,dim):
            # Currently all layers will get update_dim for all dimensions.
            self.active_dims[dim]=change['new']
            for layer in self.layers:
                layer.update_global_dims(self.active_dims)
            self.Fig.redraw()
            
        def on_time_change(change):
            # Time also hooks into saving frames
            on_coord_change(change,'time')
            if self.save_enabled.value:
                self.save_frame()

        old_dims=self.active_dims
        self.active_dims={}
        
        for dim in self.global_dims:
            coord=self.global_dims[dim]
            value = self.active_dims[dim] = old_dims.get(dim,0) # default to 0
            vmin=0
            vmax=len(coord)-1 # inclusive
            
            if dim=='time':
                # special handling for 'time'
                self.time_slider = widgets.IntSlider(min=vmin,max=vmax,value=value)
                self.play = widgets.Play(
                    value=value,
                    min=vmin,
                    max=vmax,
                    step=1,
                    interval=50,
                    description="Press play",
                    disabled=False
                )
                
                widgets.jslink((self.play, 'value'), (self.time_slider, 'value'))
                time_control=widgets.HBox([widgets.Label('Time:'),self.play, self.time_slider])
                coord_widgets.append(time_control)
                self.time_slider.observe(on_time_change, names='value')
            else:
                slider=widgets.IntSlider(min=vmin,max=vmax,value=value)
                row=widgets.HBox([widgets.Label(dim),slider])
                slider.observe(lambda change: on_coord_change(change,dim),'value')
                coord_widgets.append(row)

        return widgets.VBox(coord_widgets)
        
    def global_controls_widget(self):
        # buttons, toggles, etc. that control the global state of the figure
        # so far I'm striking out on getting this layout to work better...
        row_layout= Layout(
            display='flex',
            flex_flow='row',
            justify_content='space-between'
        )

        # Somehow CheckBoxes have a bunch of leading white space...
        # ToggleButtons seem okay, though UX is not quite as good.
        # Might be due to fixed length of descriptions, which can be overridden by
        # using a widget.Label instead.
        style = {'description_width': 'initial'}
        
        show_axes=widgets.Checkbox(value=True,description='Show axes',style=style,
                                   layout=Layout(width="40%"))
        show_axes.observe(self.on_show_axes_change,names='value')
        tight_layout=widgets.Button(description="Tight layout")
        tight_layout.on_click(self.do_tight_layout)
        self.save_enabled=widgets.Checkbox(value=False,description="Save images",
                                           style=style)
                                               
        self.save_path=widgets.Text(value='image-%04d.png',
                                    placeholder='path for saved plots',
                                    disabled=True, layout=Layout(width='60%')
        )
        # might not work - may need to do it python side.
        def cb(change,save_path=self.save_path):
            save_path.disabled=not change['new']
        self.save_enabled.observe(cb,names='value')
        
        vbox=widgets.VBox([widgets.Box([show_axes,
                                        tight_layout],layout=row_layout),
                           widgets.Box([self.save_enabled,
                                        self.save_path],
                                        layout=row_layout)])
        return vbox

    def active_dataset(self):
        return self.datasets[0] # temporary hack.
    def var_options(self):
        return self.active_dataset().available_vars()
    # dummy
    plt_options=['pseudocolor','quiver']
    def new_layer_pane(self):
        plt_selector=widgets.Dropdown(options=self.plt_options)
        var_selector=widgets.Dropdown(options=self.var_options())
        button=widgets.Button(description="Add layer")
        button.on_click(lambda b: self.add_layer(var_selector.value))
        # How to slim up this thing?
        # width=50% leaves the center pane very wide, but just uses 50%
        # of the area.
        vbox=widgets.VBox([plt_selector,var_selector,button])
        # layout=Layout(width='50%', height='80px'))
        return vbox
    
    def save_frame(self):
        fn_template=self.save_path.value
        fn=fn_template.format(**self.active_dims)
        self.Fig.save_frame(fn)
    def on_show_axes_change(self,change):
        if self.Fig is not None:
            self.Fig.ax.axis(change['new'])
    def do_tight_layout(self,b):
        self.Fig.do_tight_layout()
        
    def add_layer(self,variable,ds=None):
        if ds is None: ds=self.active_dataset()
        
        layer=ds.create_layer(variable)
        if layer is None:
            print("Could not create layer")
            return
        # will have to be smarter once active_dims actually represents
        # the active layer. and active_dims will probably show all
        # dimensions, but we'll show sliders only for the ones for the
        # active layer.
        self.layers.append(layer)
        layer.observe(self.on_free_dims_change,names=['free_dims'])
        self.on_free_dims_change(None) # 
        layer.update_global_dims(self.active_dims)
        layer.init_plot(self.Fig)
        self.activate_layer(layer)
        self.update_layer_selector()

    def on_free_dims_change(self,change):
        # can extend to be smarter later...
        # For now this is a full update to the dimension widgets
        # Update global_dims, then recreate the coordinate pane
        # also makes sure that active_dims has the right entries
        has_changed=False

        new_global_dims={}

        # Get the full collection across layers
        for layer in self.layers:
            for dim in layer.free_dims:
                # could test for collisions here.
                new_global_dims[dim] = layer.free_dims[dim]
        # Check for updates to existing
        for dim in list(self.global_dims.keys()): # safe for deleting on-the-fly
            if dim not in new_global_dims:
                del self.global_dims[dim] 
                has_changed=True
            elif new_global_dims[dim] != self.global_dims[dim]:
                self.global_dims[dim] = new_global_dims[dim]
                has_changed=True
        # And newly added dims
        for dim in new_global_dims:
            if dim not in self.global_dims:
                self.global_dims[dim] = new_global_dims[dim]
                has_changed=True
                
        if has_changed:
            print("Will update/create coordinate pane")
            # This part would be nicer if it just updated widgets as needed
            # instead of rebuilding the whole thing.
            self.coordinate_pane=self.create_coordinate_pane()
            self.header = self.coordinate_pane
                
    def on_layer_change(self,change):
        if change['new']==self.new_layer:
            self.center = self.new_layer_pane()
        else:
            self.set_active_layer_from_label(change['new'])
            if self.active_layer is not None:
                self.center = self.active_layer.layer_edit_pane()

    def set_active_layer_from_label(self,label):
        for layer in self.layers:
            if layer.label == label:
                self.active_layer=layer
                return
        self.active_layer=None
        
    def update_layer_selector(self):
        options=[self.new_layer]
        for layer in self.layers:
            options.append(layer.label)
        self.layer_selector.options=tuple(options)
        if self.active_layer is None:
            self.layer_selector.value=self.new_layer
        else:
            self.layer_selector.value=self.active_layer.label

    def activate_layer(self,layer):
        if self.active_layer == layer: return
        self.deactivate_layer()
        self.active_layer=layer

        # I think it works to just directly set these -- the traitlets
        # will take care of the rest?       
        # small problem: setting the options will force a change to the
        # value, and currently that sets a value that doesn't plot (face_node)
        # So disable the callback while changing options:
        # TODO disentangle var_selector and layer_selector
        
        #self.layer_selector.unobserve(self.on_var_change,names='value')
        #self.layer_selector.options=self.active_layer.available_vars()
        #self.layer_selector.value=layer.variable
        #self.layer_selector.observe(self.on_var_change,names='value')

    def deactivate_layer(self):
        self.active_layer=None
        
