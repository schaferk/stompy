import time
import logging
import matplotlib.pyplot as plt
import numpy as np
import field
from scipy import optimize as opt
import utils

import unstructured_grid
reload(unstructured_grid)
import exact_delaunay
reload(exact_delaunay)
import front
reload(front)

#-# Curve -

def hex_curve():
    hexagon = np.array( [[0,1],
                         [1,0],
                         [3,0],
                         [4,1],
                         [3,2],
                         [1,2]] )
    return front.Curve(10*hexagon)

def test_curve_eval():
    crv=hex_curve()
    f=np.linspace(0,2*crv.total_distance(),25)
    crvX=crv(f)
    
    if plt:
        plt.clf()
        crv.plot()

        f=np.linspace(0,crv.total_distance(),25)
        crvX=crv(f)
        plt.plot(crvX[:,0],crvX[:,1],'ro')

def test_distance_away():
    crv=hex_curve()

    if plt:
        plt.clf()
        crv.plot()
        plt.axis('equal')
        
    rtol=0.05

    for f00,tgt,style in [ (0,10,'g-'),
                           (3.4,20,'r-'),
                           (3.4,-20,'r--') ]:
        for f0 in np.linspace(f00,crv.distances[-1],20):
            x0=crv(f0)
            f,x =crv.distance_away(f0,tgt,rtol=rtol)
            d=utils.dist(x-x0)
            assert np.abs( (d-np.abs(tgt))/tgt) <= rtol
            if plt:
                plt.plot( [x0[0],x[0]],
                          [x0[1],x[1]],style)

    try:
        f,x=crv.distance_away(0.0,50,rtol=0.05)
        raise Exception("That was supposed to fail!")
    except crv.CurveException:
        #print "Okay"
        pass

def test_is_forward():
    crv=hex_curve()
    assert crv.is_forward(5,6,50)
    assert crv.is_reverse(5,-5,10)



#-# 
def test_curve_upsample():
    boundary=hex_curve()
    scale=field.ConstantField(3)

    pnts,dists = boundary.upsample(scale,return_sources=True)

    if plt:
        plt.clf()
        line=boundary.plot()
        plt.setp(line,lw=0.5,color='0.5')

        #f=np.linspace(0,crv.total_distance(),25)
        #crvX=crv(f)
        plt.scatter(pnts[:,0],pnts[:,1],30,dists,lw=0)
    
def test_basic_setup():
    boundary=hex_curve()
    af=front.AdvancingFront()
    scale=field.ConstantField(3)

    af.add_curve(boundary)
    af.set_edge_scale(scale)

    # create boundary edges based on scale and curves:
    af.initialize_boundaries()

    if plt:
        plt.clf()
        g=af.grid
        g.plot_edges()
        g.plot_nodes()

        # 
        coll=g.plot_halfedges(values=g.edges['cells'])
        coll.set_lw(0)
        coll.set_cmap('winter')
        

    return af


# Going to try more of a half-edge approach, rather than explicitly
# tracking the unpaved rings.
# hoping that a half-edge interface is sufficient for the paver, and
# could be supported by multiple representations internally.

# for starters, don't worry about caching/speed/etc.
# okay to start from scratch each time.

# the product here is a list of the N best internal angles for
# filling with a triangle(s)

def test_halfedge_traverse():
    af=test_basic_setup()
    J,Orient = np.nonzero( (af.grid.edges['cells'][:,:]==self.grid.UNMESHED) )

    # he=he0=HalfEdge(af.grid,J[0],Orient[0])
    he=he0=af.grid.halfedge(J[0],Orient[0])

    for i in range(af.grid.Nedges()*2):
        he=he.fwd()
        if he == he0:
            break
    else:
        assert False
    assert i==33 # pretty sure about that number...

    he=he0=af.grid.halfedge(J[0],Orient[0])

    for i in range(af.grid.Nedges()*2):
        he=he.rev()
        if he == he0:
            break
    else:
        assert False
    assert i==33 # pretty sure about that number...


    assert he.fwd().rev() == he
    assert he.rev().fwd() == he
    #-# 

def test_merge_edges():
    af=test_basic_setup()

    new_j=af.grid.merge_edges(node=0)
    
    he0=he=af.grid.halfedge(new_j,0)
    c0_left = af.grid.edges['cells'][he.j,he.orient]
    c0_right = af.grid.edges['cells'][he.j,1-he.orient]

    while True:
        he=he.fwd()
        c_left = af.grid.edges['cells'][he.j,he.orient]
        c_right = af.grid.edges['cells'][he.j,1-he.orient]
        assert c_left==c0_left
        assert c_right==c0_right
        
        if he==he0:
            break


    if plt:
        plt.clf()
        af.grid.plot_edges()

        coll=af.grid.plot_halfedges(values=af.grid.edges['cells'])
        coll.set_lw(0)
        coll.set_cmap('winter')

# when resample nodes on a sliding boundary, want to calculate the available
# span, and if it's small, start distributing the nodes evenly.
# where small is defined by local_scale * max_span_factor


def test_resample():
    af=test_basic_setup()
    a=0
    b=af.grid.node_to_nodes(a)[0]
    he=af.grid.nodes_to_halfedge(a,b)
    anchor=he.node_rev()
    n=he.node_fwd()
    n2=he.rev().node_rev()
    af.resample(n=n,anchor=anchor,scale=25,direction=1)
    af.resample(n=n2,anchor=anchor,scale=25,direction=-1)

    if plt:
        plt.clf()
        af.grid.plot_edges()

        coll=af.grid.plot_halfedges(values=af.grid.edges['cells'])
        coll.set_lw(0)
        coll.set_cmap('winter')
    
    
#-#     



def test_resample_neighbors():
    af=test_basic_setup()
    
    if plt:
        plt.clf()
        af.grid.plot_nodes(color='r')
    
    site=af.choose_site()
            
    af.resample_neighbors(site)

    if plt:
        af.grid.plot_edges()

        af.grid.plot_nodes(color='g')
        # hmm - some stray long edges, where it should be collinear
        # ahh - somehow node 23 is 3.5e-15 above the others.
        # not sure why it happened, but for the moment not a show stopper.
        # in fact probably a good test of the robust predicates
        af.cdt.plot_edges(values=af.cdt.edges['constrained'],lw=3,alpha=0.5)

        plt.axis( [34.91, 42.182, 7.300, 12.97] )
    return af
        
# af=test_resample_neighbors()

# enumerate the strategies for a site:
# paver preemptively resamples the neighbors
# conceivable that one action might want to resample the neighbors
# in a slightly different way than another action.
# but the idea of having them spaced at the local scale when possible
# is general enough to do it preemptively.

# strategies:
#  try this as a separate class for each strategy, but they are all singletons


def test_actions():
    af=test_basic_setup()

    site=af.choose_site()
    af.resample_neighbors(site)
    actions=site.actions()
    metrics=[a.metric(site) for a in actions]
    best=np.argmin(metrics)
    edits=actions[best].execute(site)
    af.optimize_edits(edits)

# #

af=test_basic_setup()
check0=af.grid.checkpoint()


##

# without profiling, it's 20s, 8.5 cells/s (with delaunay checks)
# without delaunay checks, that becomes 11 cells/s.
# if it had bisect, maybe we could get to 20 cells/s?

# okay - so with bisect, and the necessary other tweaks, it
# can now finish, and do so at 14 cells/s.


af=test_basic_setup()
af.log.setLevel(logging.INFO)

af.cdt.post_check=False

t_start=time.time()
## 
af.loop()  
elapsed=time.time() - t_start
print "Elapsed: %.2fs, or %f cells/s"%(elapsed,af.grid.Ncells()/elapsed)

plt.figure(1).clf()
af.plot_summary(label_nodes=False)

##

# having added bisect, a few fixes, tweaks, 
# where is the bulk of the time?
# 11.82 seconds for 144 cells

# 4.8s for optimize - nelder-mead
#   3.6 for 30k calls to one_point_cost

# 1.3s for 305k calls to numpy.reduce??

# 2.2s in topo_sort_adjacent_nodes
# 3.3s in propagating_flip
# 1.2s in nodes_to_halfedge
# 5.5s in modify_node

# something like 0.35s total for robust predicates

# only 0.6s for locate - but this will get worse at scale
#  until we include passing in a starting point

# So making modify_node smarter could save up to 50%


## 
# I think the best plan of attack is to roughly replicate the way paver
# worked, then extend with the graph search

#   Need to think about how these pieces are going to work together
#   And probably a good time to (a) start adding the rollback, graph
#   search side of things.
#   CDT is included now, and can trigger an alternate strategy when
#   edges intersect.  No non-local connections, though.

site3=af.choose_site()

if plt:
    plt.clf()
    af.grid.plot_edges()

    coll=af.grid.plot_halfedges(values=af.grid.edges['cells'])
    coll.set_lw(0)
    coll.set_cmap('winter')
    site3.plot()


###


# 205 cells in 31s: 6.5 cells/sec.
##


af=test_basic_setup()
af.zoom= (32.227882629771564, 42.939919477502656, 5.7123819361683283, 14.064748439519079)


af.loop(count=1)
site=af.choose_site()
af.resample_neighbors(site)


## 

plt.figure(1).clf()
fig,ax=plt.subplots(num=1)
af.plot_summary()
af.grid.plot_cells(facecolor='0.8',edgecolor='w',lw=8,zorder=-5)
site.plot()

