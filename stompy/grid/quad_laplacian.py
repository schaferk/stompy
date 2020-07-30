"""
Create a nearly orthogonal quad mesh by solving for stream function
and velocity potential inside a given boundary.
"""

import numpy as np
from shapely import geometry
from scipy import sparse

import matplotlib.pyplot as plt

from . import unstructured_grid, exact_delaunay,orthogonalize
from .. import utils
from ..spatial import field
from . import front

import six

##

# borrow codes as in front.py
RIGID=front.AdvancingFront.RIGID

class NodeDiscretization(object):
    def __init__(self,g):
        self.g=g
    def construct_matrix(self,op='laplacian',dirichlet_nodes={},
                         zero_tangential_nodes=[]):
        """
        Construct a matrix and rhs for the given operation.
        dirichlet_nodes: boundary node id => value
        zero_tangential_nodes: list of lists.  each list gives a set of
          nodes which should be equal to each other, allowing specifying
          a zero tangential gradient BC.  
        """
        g=self.g
        B=np.zeros(g.Nnodes(),np.float64)
        M=sparse.dok_matrix( (g.Nnodes(),g.Nnodes()),np.float64)

        # Adjust tangential node data structure for easier use
        # in matrix construction
        tangential_nodes={} 
        for grp in zero_tangential_nodes:
            leader=grp[0]
            for member in grp:
                # NB: This includes leader=>leader
                tangential_nodes[member]=leader

        for n in range(g.Nnodes()):
            if n in dirichlet_nodes:
                nodes=[n]
                alphas=[1]
                rhs=dirichlet_nodes[n]
            elif n in tangential_nodes:
                leader=tangential_nodes[n]
                if n==leader:
                    # Really should drop the row
                    rhs=0.0
                    nodes=[n]
                    alphas=[0]
                else:
                    rhs=0.0
                    nodes=[n,leader]
                    alphas=[1,-1]
            else:
                nodes,alphas,rhs=self.node_discretization(n,op=op)
                # could add to rhs here
            B[n]=rhs
            for node,alpha in zip(nodes,alphas):
                M[n,node]=alpha
        return M,B
    
    def node_laplacian(self,n0):
        return self.node_discretization(n0,'laplacian')

    def node_dx(self,n0):
        return self.node_discretization(n0,'dx')
    
    def node_discretization(self,n0,op='laplacian'):
        def beta(c):
            return 1.0
        
        N=self.g.angle_sort_adjacent_nodes(n0)
        P=len(N)
        is_boundary=int(self.g.is_boundary_node(n0))
        M=len(N) - is_boundary

        if is_boundary:
            # roll N to start and end on boundary nodes:
            nbr_boundary=[self.g.is_boundary_node(n)
                          for n in N]
            while not (nbr_boundary[0] and nbr_boundary[-1]):
                N=np.roll(N,1)
                nbr_boundary=np.roll(nbr_boundary,1)
        
        # area of the triangles
        A=[] 
        for m in range(M):
            tri=[n0,N[m],N[(m+1)%P]]
            Am=utils.signed_area( self.g.nodes['x'][tri] )
            A.append(Am)
        AT=np.sum(A)

        alphas=[]
        x=self.g.nodes['x'][N,0]
        y=self.g.nodes['x'][N,1]
        x0,y0=self.g.nodes['x'][n0]
        
        for n in range(P):
            n_m_e=(n-1)%M
            n_m=(n-1)%P
            n_p=(n+1)%P
            a=0
            if op=='laplacian':
                if n>0 or P==M: # nm<M
                    a+=-beta(n_m_e)/(4*A[n_m_e]) * ( (y[n_m]-y[n])*(y0-y[n_m]) + (x[n] -x[n_m])*(x[n_m]-x0))
                if n<M:
                    a+= -beta(n)/(4*A[n])  * ( (y[n]-y[n_p])*(y[n_p]-y0) + (x[n_p]-x[n ])*(x0 - x[n_p]))
            elif op=='dx':
                if n>0 or P==M: # nm<M
                    a+= beta(n_m_e)/(2*AT) * (y0-y[n_m])
                if n<M:
                    a+= beta(n)/(2*AT) * (y[n_p]-y0)
            elif op=='dy':
                if n>0 or P==M: # nm<M
                    a+= beta(n_m_e)/(2*AT) * (x[n_m]-x0)
                if n<M:
                    a+= beta(n)/(2*AT) * (x0 - x[n_p])
            else:
                raise Exception('bad op')
                
            alphas.append(a)

        alpha0=0
        for e in range(M):
            ep=(e+1)%P
            if op=='laplacian':
                alpha0+= - beta(e)/(4*A[e]) * ( (y[e]-y[ep])**2 + (x[ep]-x[e])**2 )
            elif op=='dx':
                alpha0+= beta(e)/(2*AT)*(y[e]-y[ep])
            elif op=='dy':
                alpha0+= beta(e)/(2*AT)*(x[ep]-x[e])
            else:
                raise Exception('bad op')
                
        if op=='laplacian' and P>M:
            norm_grad=0 # no flux bc
            L01=np.sqrt( (x[0]-x0)**2 + (y0-y[0])**2 )
            L0P=np.sqrt( (x[0]-x[-1])**2 + (y0-y[-1])**2 )

            gamma=3/AT * ( beta(0) * norm_grad * L01/2
                           + beta(P-1) * norm_grad * L0P/2 )
        else:
            gamma=0
        assert np.isfinite(alpha0)
        return ([n0]+list(N),
                [alpha0]+list(alphas),
                -gamma)

class QuadsGen(object):
    """
    Handle a single generating grid with multiple quad patches.
    Mostly dispatches subgrids to QuadGen
    """
    def __init__(self,gen,seeds=None,cells=None,**kwargs):
        self.qgs=[]
        
        if seeds is not None:
            raise Exception('Seeds would be nice but not yet implemented')
        if cells is None:
            cells=range(gen.Ncells())

        self.g_final=None
        for cell in cells:
            loc_gen=gen.copy()
            for c in range(gen.Ncells()):
                if c!=cell:
                    loc_gen.delete_cell(c)
            loc_gen.delete_orphan_edges()
            loc_gen.delete_orphan_nodes()
            loc_gen.renumber(reorient_edges=False)

            qg=QuadGen(gen=loc_gen,**kwargs)
            self.qgs.append(qg)
            try:
                loc_g_final=qg.g_final
            except AttributeError:
                # Probably didn't execute
                continue 
            if self.g_final is None:
                self.g_final=loc_g_final
            else:
                self.g_final.add_grid(loc_g_final,merge_nodes='auto')
                
    def plot_result(self,num=5):
        plt.figure(num).clf()
        self.g_final.plot_edges()
        plt.axis('tight')
        plt.axis('equal')
    
class QuadGen(object):
    
    # default behavior computes a nominal, isotropic grid for the calculation
    #  of the orthogonal mapping, and then a separate anisotropic grid for the
    #  final result. If anisotropic is False, the isotropic grid is kept, 
    #  and its ij indices will be updated to reflect the anisotropic inputs.
    anisotropic=True

    # The cell spacing in geographic coordinates for the nominal, isotropic grid
    nom_res=4.0
    # Minimum number of edges along a boundary segment in the nominal isotropic grid
    min_steps=2
    
    def __init__(self,gen,execute=True,**kw):
        """
        gen: the design grid. cells of this grid will be filled in with 
        quads.  
        nodes should have separate i and j fields.

        i,j are interpeted as x,y indices in the reference frame of the quad grid.

        execute: if True, run the full generation process.  Otherwise preprocess
          inputs but do not solve.
        """
        utils.set_keywords(self,kw)
        gen=gen.copy()
        self.gen=gen

        # Prep the target resolution grid information
        self.coalesce_ij(self.gen)
        self.fill_ij_interp(self.gen)
        self.node_ij_to_edge(self.gen)

        # Prep the nominal resolution grid information
        self.coalesce_ij_nominal(self.gen,dest='IJ')
        self.fill_ij_interp(self.gen,dest='IJ')
        self.node_ij_to_edge(self.gen,dest='IJ')

        if execute:
            self.add_bezier(self.gen)
            self.g_int=self.create_intermediate_grid(src='IJ')
            # This now happens as a side effect of smooth_interior_quads
            # self.adjust_intermediate_bounds()
            self.smooth_interior_quads(self.g_int)
            self.calc_psi_phi()
            if self.anisotropic:
                self.g_final=self.create_intermediate_grid(src='ij',coordinates='ij')
                self.adjust_by_psi_phi(self.g_final, src='ij')
            else:
                self.g_final=self.g_int.copy()
                self.adjust_by_psi_phi(self.g_final, src='IJ')
                # but update ij to reflect the 'ij' in the original input.
                ij=self.remap_ij(self.g_final,src='ij')
                self.g_final.nodes['ij']=ij

    def node_ij_to_edge(self,g,dest='ij'):
        dij=(g.nodes[dest][g.edges['nodes'][:,1]]
             - g.nodes[dest][g.edges['nodes'][:,0]])
        g.add_edge_field('d'+dest,dij,on_exists='overwrite')

    
    def coalesce_ij_nominal(self,gen,dest='IJ',nom_res=None,min_steps=None,
                            max_cycle_len=1000):
        """ 
        Similar to coalesce_ij(), but infers a scale for I and J
        from the geographic distances.
        nom_res: TODO -- figure out good default.  This is the nominal
         spacing for i and j in geographic units.
        min_steps: edges should not be shorter than this in IJ space.

        max_cycle_len: only change for large problems.  Purpose here
          is to abort on bad inputs/bugs instead of getting into an
          infinite loop
        """
        if nom_res is None:
            nom_res=self.nom_res
        if min_steps is None:
            min_steps=self.min_steps
            
        IJ=np.zeros( (gen.Nnodes(),2), np.float64)
        IJ[...]=np.nan

        # Very similar to fill_ij_interp, but we go straight to
        # assigning dIJ
        cycles=gen.find_cycles(max_cycle_len=1000)

        assert len(cycles)==1,"For now, cannot handle multiple cycles"

        ij_in=np.c_[ gen.nodes['i'], gen.nodes['j'] ]
        ij_fixed=np.isfinite(ij_in)

        # Collect the steps so that we can close the sum at the end
        for idx in [0,1]: # i, j
            steps=[] # [ [node a, node b, delta], ... ]
            for s in cycles:
                # it's a cycle, so we can roll
                is_fixed=np.nonzero( ij_fixed[s,idx] )[0]
                assert len(is_fixed),"There are no nodes with fixed i,j!"
                
                s=np.roll(s,-is_fixed[0])
                s=np.r_[s,s[0]] # repeat first node at the end
                # Get the new indices for fixed nodes
                is_fixed=np.nonzero( ij_fixed[s,idx] )[0]

                dists=utils.dist_along( gen.nodes['x'][s] )

                for a,b in zip( is_fixed[:-1],is_fixed[1:] ):
                    d_ab=dists[b]-dists[a]
                    dij_ab=ij_in[s[b],idx] - ij_in[s[a],idx]
                    if dij_ab==0:
                        steps.append( [s[a],s[b],0] )
                    else:
                        n_steps=max(min_steps,d_ab/nom_res)
                        dIJ_ab=int( np.sign(dij_ab) * n_steps )
                        steps.append( [s[a],s[b],dIJ_ab] )
                steps=np.array(steps)
                err=steps[:,2].sum()

                stepsizes=np.abs(steps[:,2])
                err_dist=np.round(err*np.cumsum(np.r_[0,stepsizes])/stepsizes.sum())
                err_per_step = np.diff(err_dist)
                steps[:,2] -= err_per_step.astype(np.int32)

            # Now steps properly sum to 0.
            IJ[steps[0,0],idx]=0 # arbitrary starting point
            IJ[steps[:-1,1],idx]=np.cumsum(steps[:-1,2])

        gen.add_node_field(dest,IJ,on_exists='overwrite')
        gen.add_node_field(dest+'_fixed',ij_fixed,on_exists='overwrite')

    def coalesce_ij(self,gen,dest='ij'):
        """
        Copy incoming 'i' and 'j' node fields to 'ij', and note which
        values were finite in 'ij_fixed'
        """
        ij_in=np.c_[ gen.nodes['i'], gen.nodes['j'] ]
        gen.add_node_field(dest,ij_in,on_exists='overwrite')
        gen.add_node_field(dest+'_fixed',np.isfinite(ij_in))
        
    def fill_ij_interp(self,gen,dest='ij'):
        """
        Interpolate the values in gen.nodes[dest] evenly between
        the existing fixed values
        """
        # the rest are filled by linear interpolation
        strings=gen.extract_linear_strings()

        for idx in [0,1]:
            node_vals=gen.nodes[dest][:,idx] 
            for s in strings:
                # This has some 'weak' support for multiple cycles. untested.
                if s[0]==s[-1]:
                    # cycle, so we can roll
                    has_val=np.nonzero( np.isfinite(node_vals[s]) )[0]
                    if len(has_val):
                        s=np.roll(s[:-1],-has_val[0])
                        s=np.r_[s,s[0]]
                s_vals=node_vals[s]
                dists=utils.dist_along( gen.nodes['x'][s] )
                valid=np.isfinite(s_vals)
                fill_vals=np.interp( dists[~valid],
                                     dists[valid], s_vals[valid] )
                node_vals[s[~valid]]=fill_vals
            
    def create_intermediate_grid(self,src='ij',coordinates='xy'):
        """
        src: base variable name for the ij indices to use.
          i.e. gen.nodes['ij'], gen.nodes['ij_fixed'],
             and gen.edges['dij']

          the resulting grid will use 'ij' regardless, this just for the
          generating grid.

        coordinates: 
         'xy' will interpolate the gen xy coordinates to get
          node coordinates for the result.
         'ij' will leave 'ij' coordinate values in both x and 'ij'
        
        """
        # target grid
        g=unstructured_grid.UnstructuredGrid(max_sides=4,
                                             extra_node_fields=[('ij',np.float64,2),
                                                                ('gen_j',np.int32),
                                                                ('rigid',np.int32)])
        gen=self.gen
        for c in gen.valid_cell_iter():
            local_edges=gen.cell_to_edges(c,ordered=True)
            flip=(gen.edges['cells'][local_edges,0]!=c)
            
            edge_nodes=gen.edges['nodes'][local_edges]
            edge_nodes[flip,:] = edge_nodes[flip,::-1]

            dijs=gen.edges['d'+src][local_edges] * ((-1)**flip)[:,None]
            xys=gen.nodes['x'][edge_nodes[:,0]]
            ij0=gen.nodes[src][edge_nodes[0,0]]
            ijs=np.cumsum(np.vstack([ij0,dijs]),axis=0)

            # Sanity check to be sure that all the dijs close the loop.
            assert np.allclose( ijs[0],ijs[-1] )

            ijs=np.array(ijs[:-1])
            # Actually don't, so that g['ij'] and gen['ij'] match up.
            # ijs-=ijs.min(axis=0) # force to have ll corner at (0,0)
            ij0=ijs.min(axis=0)
            ijN=ijs.max(axis=0)
            ij_size=ijN-ij0

            # Create in ij space
            patch=g.add_rectilinear(p0=ij0,
                                    p1=ijN,
                                    nx=int(1+ij_size[0]),
                                    ny=int(1+ij_size[1]))
            pnodes=patch['nodes'].ravel()

            g.nodes['gen_j'][pnodes]=-1

            # Copy xy to ij, then optionally remap xy
            g.nodes['ij'][pnodes] = g.nodes['x'][pnodes]

            if coordinates=='xy':
                Extrap=utils.LinearNDExtrapolator

                int_x=Extrap(ijs,xys[:,0])
                node_x=int_x(g.nodes['x'][pnodes,:])

                int_y=Extrap(ijs,xys[:,1])
                node_y=int_y(g.nodes['x'][pnodes,:])

                g.nodes['x'][pnodes]=np.c_[node_x,node_y]

                # delete cells that fall outside of the ij
                # This seems wrong, though. Using Extrap,
                # this is only nan when the Extrapolation didn't
                # extrapolate enough.  The real trimming is in
                # 'ij' space below.
                # for n in pnodes[ np.isnan(node_x) ]:
                #     g.delete_node_cascade(n)

            ij_poly=geometry.Polygon(ijs)
            for cc in patch['cells'].ravel():
                if g.cells['deleted'][cc]: continue
                cn=g.cell_to_nodes(cc)
                c_ij=np.mean(g.nodes['ij'][cn],axis=0)
                if not ij_poly.contains(geometry.Point(c_ij)):
                    g.delete_cell(cc)
            # This part will need to get smarter when there are multiple patches:
            g.delete_orphan_edges()
            g.delete_orphan_nodes()

            # Mark nodes as rigid if they match a point in the generator
            for n in g.valid_node_iter():
                match0=gen.nodes[src][:,0]==g.nodes['ij'][n,0]
                match1=gen.nodes[src][:,1]==g.nodes['ij'][n,1]
                match=np.nonzero(match0&match1)[0]
                if len(match):
                    g.nodes['rigid'][n]=RIGID

            # Fill in generating edges for boundary nodes
            boundary_nodes=g.boundary_cycle()
            # hmm -
            # each boundary node in g sits at either a node or
            # edge of gen.
            # For any non-rigid node in g, it should sit on
            # an edge of gen.  ties can go either way, doesn't
            # matter (for bezier purposes)
            # Can int_x/y help here?
            # or just brute force it
            
            local_edge_ijs=np.array( [ ijs, np.roll(ijs,-1,axis=0)] )
            lower_ij=local_edge_ijs.min(axis=0)
            upper_ij=local_edge_ijs.max(axis=0)
            
            for n in boundary_nodes:
                n_ij=g.nodes['ij'][n]

                # [ {nA,nB}, n_local_edges, {i,j}]
                candidates=np.all( (n_ij>=lower_ij) & (n_ij<=upper_ij),
                                   axis=1)
                for lj in np.nonzero(candidates)[0]:
                    # is n_ij approximately on the line
                    # local_edge_ijs[lj] ?
                    offset=utils.point_line_distance(n_ij,local_edge_ijs[:,lj,:])
                    if offset<0.1:
                        g.nodes['gen_j'][n]=local_edges[lj]
                        break
                else:
                    raise Exception("Failed to match up a boundary node")
                
        g.renumber()
        return g
    
    def plot_intermediate(self,num=1):
        plt.figure(num).clf()
        fig,ax=plt.subplots(num=num)
        self.gen.plot_edges(lw=1.5,color='b',ax=ax)
        self.g_int.plot_edges(lw=0.5,color='k',ax=ax)

        self.g_int.plot_nodes(mask=self.g_int.nodes['rigid']>0)
        ax.axis('tight')
        ax.axis('equal')

    def add_bezier(self,gen):
        """
        Generate bezier control points for each edge.
        """
        # Need to force the corners to be 90deg angles, otherwise
        # there's no hope of getting orthogonal cells in the interior.
        
        order=3 # cubic bezier curves
        bez=np.zeros( (gen.Nedges(),order+1,2) )
        bez[:,0,:] = gen.nodes['x'][gen.edges['nodes'][:,0]]
        bez[:,order,:] = gen.nodes['x'][gen.edges['nodes'][:,1]]

        gen.add_edge_field('bez', bez, on_exists='overwrite')

        for n in gen.valid_node_iter():
            js=gen.node_to_edges(n)
            assert len(js)==2
            # orient the edges
            njs=[]
            deltas=[]
            dijs=[]
            flips=[]
            for j in js:
                nj=gen.edges['nodes'][j]
                dij=gen.edges['dij'][j]
                flip=0
                if nj[0]!=n:
                    nj=nj[::-1]
                    dij=-dij
                    flip=1
                assert nj[0]==n
                njs.append(nj)
                dijs.append(dij)
                flips.append(flip)
                deltas.append( gen.nodes['x'][nj[1]] - gen.nodes['x'][nj[0]] )
            # now node n's two edges are in njs, as node pairs, with the first
            # in each pair being n
            # dij is the ij delta along that edge
            # flip records whether it was necessary to flip the edge
            # and deltas records the geometry delta
            
            # the angle in ij space tells us what it *should* be
            # these are angles going away from n
            # How does this work out when it's a straight line in ij space?
            theta0_ij=np.arctan2( -dijs[0][1], -dijs[0][0])
            theta1_ij=np.arctan2(dijs[1][1],dijs[1][0]) 
            dtheta_ij=(theta1_ij - theta0_ij + np.pi) % (2*np.pi) - np.pi

            theta0=np.arctan2(-deltas[0][1],-deltas[0][0])
            theta1=np.arctan2(deltas[1][1],deltas[1][0])
            dtheta=(theta1 - theta0 + np.pi) % (2*np.pi) - np.pi

            theta_err=dtheta-dtheta_ij # 103: -0.346, slight right but should be straight
            #theta0_adj = theta0+theta_err/2
            #theta1_adj = theta1-theta_err/2

            # not sure about signs here.
            cp0 = gen.nodes['x'][n] + utils.rot( theta_err/2, 1./3 * deltas[0] )
            cp1 = gen.nodes['x'][n] + utils.rot( -theta_err/2, 1./3 * deltas[1] )

            # save to the edge
            gen.edges['bez'][js[0],1+flips[0]] = cp0
            gen.edges['bez'][js[1],1+flips[1]] = cp1

    def plot_gen_bezier(self,num=10):
        gen=self.gen
        fig=plt.figure(num)
        fig.clf()
        ax=fig.add_subplot(1,1,1)
        gen.plot_edges(lw=0.3,color='k',alpha=0.5,ax=ax)
        gen.plot_nodes(alpha=0.5,ax=ax,zorder=3,color='orange')
        
        for j in self.gen.valid_edge_iter():
            n0=gen.edges['nodes'][j,0]
            nN=gen.edges['nodes'][j,1]
            bez=gen.edges['bez'][j]
            
            t=np.linspace(0,1,21)

            B0=(1-t)**3
            B1=3*(1-t)**2 * t
            B2=3*(1-t)*t**2
            B3=t**3
            points = B0[:,None]*bez[0] + B1[:,None]*bez[1] + B2[:,None]*bez[2] + B3[:,None]*bez[3]

            ax.plot(points[:,0],points[:,1],'r-')
            ax.plot(bez[:,0],bez[:,1],'b-o')

    def gen_bezier_curve(self,samples_per_edge=10):
        points=self.gen_bezier_linestring(samples_per_edge=samples_per_edge)
        return front.Curve(points,closed=True)
        
    def gen_bezier_linestring(self,samples_per_edge=10):
        """
        Calculate an up-sampled linestring for the bezier boundary of self.gen
        """
        gen=self.gen
        bound_nodes=self.gen.boundary_cycle()

        points=[]
        for a,b in zip(bound_nodes,np.roll(bound_nodes,-1)):
            j=gen.nodes_to_edge(a,b)
            
            n0=gen.edges['nodes'][j,0]
            nN=gen.edges['nodes'][j,1]
            bez=gen.edges['bez'][j]
            
            t=np.linspace(0,1,1+samples_per_edge)
            if n0==b: # have to flip order
                t=t[::-1]

            B0=(1-t)**3
            B1=3*(1-t)**2 * t
            B2=3*(1-t)*t**2
            B3=t**3
            edge_points = B0[:,None]*bez[0] + B1[:,None]*bez[1] + B2[:,None]*bez[2] + B3[:,None]*bez[3]

            points.append(edge_points[:-1])
        return np.concatenate(points,axis=0)

    def adjust_intermediate_bounds(self):
        """
        Adjust exterior of intermediate grid with bezier
        curves
        """
        gen=self.gen
        g=self.g_int.copy()

        # This one gets tricky with the floating-point ij values.
        # gen.nodes['ij'] may be float valued.
        # The original code iterates over gen edges, assumes that
        # Each gen edge divides to an exact number of nodes, then
        # we know the exact ij of those nodes,
        # pre-evaluate the spline and then just find the corresponding
        # nodes.

        # With float-valued gen.nodes['ij'], though, we still have
        # a bezier curve, but it's ends may not be on integer values.
        # The main hurdle is that we need a different way of associating
        # nodes in self.g to a generating edge
        
        for j in gen.valid_edge_iter():
            n0=gen.edges['nodes'][j,0]
            nN=gen.edges['nodes'][j,1]
            bez=gen.edges['bez'][j]
            
            g_nodes=np.nonzero( g.nodes['gen_j']==j )[0]

            p0=gen.nodes['x'][n0]
            pN=gen.nodes['x'][nN]

            T=utils.dist(pN-p0)
            t=utils.dist( g.nodes['x'][g_nodes] - p0 ) / T

            too_low=(t<0)
            too_high=(t>1)
            if np.any(too_low):
                print("Some low")
            if np.any(too_high):
                print("Some high")
                
            t=t.clip(0,1)

            if 1: # the intended bezier way:
                B0=(1-t)**3
                B1=3*(1-t)**2 * t
                B2=3*(1-t)*t**2
                B3=t**3
                points = B0[:,None]*bez[0] + B1[:,None]*bez[1] + B2[:,None]*bez[2] + B3[:,None]*bez[3]
            else: # debugging linear way
                print("Debugging - no bezier boundary")
                points=(1-t)[:,None]*p0 + t[:,None]*pN

            for n,point in zip(g_nodes,points):
                g.modify_node(n,x=point)

    def smooth_interior_quads(self,g,iterations=3):
        """
        Smooth quad grid by allowing boundary nodes to slide, and
        imparting a normal constraint at the boundary.
        """
        # So the anisotropic smoothing has a weakness where the spacing
        # of boundary nodes warps the interior.
        # Currently I smooth x and y independently, using the same matrix.

        # But is there a way to locally linearize where slidable boundary nodes
        # can fall, forcing their internal edge to be perpendicular to the boundary?

        # For a sliding boundary node [xb,yb] , it has to fall on a line, so
        # c1*xb + c2*yb = c3
        # where [c1,c2] is a normal vector of the line

        # And I want the edge to its interior neighbor (xi,yi) perpendicular to that line.
        # (xb-xi)*c1 + (yb-yi)*c2 = 0
        
        curve=self.gen_bezier_curve()
        
        N=g.Nnodes()

        for slide_it in utils.progress(range(iterations)):
            M=sparse.dok_matrix( (2*N,2*N), np.float64)

            rhs=np.zeros(2*N,np.float64)

            for n in g.valid_node_iter():
                if g.is_boundary_node(n):
                    dirichlet=g.nodes['rigid'][n]
                    #dirichlet=True
                    if dirichlet:
                        M[n,n]=1
                        rhs[n]=g.nodes['x'][n,0]
                        M[N+n,N+n]=1
                        rhs[N+n]=g.nodes['x'][n,1]
                    else:
                        # figure out the normal from neighbors.
                        boundary_nbrs=[]
                        interior_nbr=[]
                        for nbr in g.node_to_nodes(n):
                            if g.nodes['gen_j'][nbr]>=0:
                                boundary_nbrs.append(nbr)
                            else:
                                interior_nbr.append(nbr)
                        assert len(boundary_nbrs)==2
                        assert len(interior_nbr)==1

                        vec=np.diff( g.nodes['x'][boundary_nbrs], axis=0)[0]
                        nrm=utils.to_unit( np.array([vec[1],-vec[0]]) )
                        tng=utils.to_unit( np.array(vec) )
                        c3=np.dot(nrm,g.nodes['x'][n])
                        # n-equation puts it on the linen
                        M[n,n]=nrm[0]
                        M[n,N+n]=nrm[1]
                        rhs[n]=c3
                        # N+n equation set the normal
                        # the edge to interior neighbor (xi,yi) perpendicular to that line.
                        # (xb-xi)*c1 + (yb-yi)*c2 = 0
                        # c1*xb - c1*xi + c2*yb - c2*yi = 0
                        inbr=interior_nbr[0]
                        M[N+n,n]=tng[0]
                        M[N+n,inbr]=-tng[0]
                        M[N+n,N+n]=tng[1]
                        M[N+n,N+inbr]=-tng[1]
                        rhs[N+n]=0.0
                else:
                    nbrs=g.node_to_nodes(n)
                    if 0: # isotropic
                        M[n,n]=-len(nbrs)
                        M[N+n,N+n]=-len(nbrs)
                        for nbr in nbrs:
                            M[n,nbr]=1
                            M[N+n,N+nbr]=1
                    else:
                        # In the weighting, want to normalize by distances
                        i_length=0
                        j_length=0
                        dists=utils.dist(g.nodes['x'][n],g.nodes['x'][nbrs])
                        ij_deltas=np.abs(g.nodes['ij'][n] - g.nodes['ij'][nbrs])
                        # length scales for i and j
                        ij_scales=1./( (ij_deltas*dists[:,None]).sum(axis=0) )

                        assert np.all( np.isfinite(ij_scales) )

                        for nbr,ij_delta in zip(nbrs,ij_deltas):
                            fac=(ij_delta*ij_scales).sum()
                            M[n,nbr]=fac
                            M[n,n]-=fac
                            M[N+n,N+nbr]=fac
                            M[N+n,N+n]-=fac

            new_xy=sparse.linalg.spsolve(M.tocsr(),rhs)

            g.nodes['x'][:,0]=new_xy[:N]
            g.nodes['x'][:,1]=new_xy[N:]

            # And nudge the boundary nodes back onto the boundary
            for n in g.valid_node_iter():
                if g.nodes['gen_j'][n]>=0:
                    new_f=curve.point_to_f(g.nodes['x'][n],rel_tol='best')
                    g.nodes['x'][n] = curve(new_f)

        return g

    def bezier_boundary_polygon(self):
        """
        For trimming nodes that got shifted outside the proper boundary
        """
        # This would be more efficient if unstructured_grid just provided
        # some linestring methods that accepted a node mask
        
        g_tri=self.g_int.copy()
        internal_nodes=g_tri.nodes['gen_j']<0
        for n in np.nonzero(internal_nodes)[0]:
            g_tri.delete_node_cascade(n)
        boundary_linestring = g_tri.extract_linear_strings()[0]
        boundary=g_tri.nodes['x'][boundary_linestring]
        return geometry.Polygon(boundary)
        
    def calc_psi_phi(self):
        gtri=self.g_int
        self.nd=nd=NodeDiscretization(gtri)

        e2c=gtri.edge_to_cells()

        # psi and phi are both computed by solving the Laplacian
        # on the intermediate grid. Input values of i,j in the input
        # are used to identify strings of boundary nodes with the same
        # value (zero tangential gradient), and this constraint is
        # encoded in the matrix. This leaves the system under-determined,
        # 2*nedges too few constraints.  Three additional constraints
        # come from setting the scale and location of psi and the location
        # of phi.
        # It is still a bit unclear what the remaining degrees of freedom
        # are, but they can, in practice, be eliminated by additionally
        # the coupling terms d psi /dy ~ d phi/dx, and vice versa.
        
        # check boundaries and determine where Laplacian BCs go
        boundary=e2c.min(axis=1)<0
        i_dirichlet_nodes={} # for psi
        j_dirichlet_nodes={} # for phi

        # Block of nodes with a zero-tangential-gradient BC
        i_tan_groups=[]
        j_tan_groups=[]
        i_tan_groups_i=[] # the input i value
        j_tan_groups_j=[] # the input j value

        # Try zero-tangential-gradient nodes.  Current code will be under-determined
        # without the derivative constraints.
        bcycle=gtri.boundary_cycle()
        n1=bcycle[-1]
        i_grp=None
        j_grp=None

        for n2 in bcycle:
            i1=gtri.nodes['ij'][n1,0]
            i2=gtri.nodes['ij'][n2,0]
            j1=gtri.nodes['ij'][n1,1]
            j2=gtri.nodes['ij'][n2,1]
            if i1==i2:
                if i_grp is None:
                    i_grp=[n1]
                    i_tan_groups.append(i_grp)
                    i_tan_groups_i.append(i1)
                    j_grp=None
                i_grp.append(n2)
            elif j1==j2:
                if j_grp is None:
                    j_grp=[n1]
                    j_tan_groups.append(j_grp)
                    j_tan_groups_j.append(j1)
                    i_grp=None
                j_grp.append(n2)
            else:
                print("Don't know how to deal with non-cartesian edges")
            n1=n2

        # Set the range of psi to [-1,1], and pin some j to 1.0
        low_i=np.argmin(i_tan_groups_i)
        high_i=np.argmax(i_tan_groups_i)

        i_dirichlet_nodes[i_tan_groups[low_i][0]]=-1
        i_dirichlet_nodes[i_tan_groups[high_i][0]]=1
        j_dirichlet_nodes[j_tan_groups[1][0]]=1

        Mblocks=[]
        Bblocks=[]
        if 1: # PSI
            M_psi_Lap,B_psi_Lap=nd.construct_matrix(op='laplacian',
                                                    dirichlet_nodes=i_dirichlet_nodes,
                                                    zero_tangential_nodes=i_tan_groups)
            Mblocks.append( [M_psi_Lap,None] )
            Bblocks.append( B_psi_Lap )
        if 1: # PHI
            M_phi_Lap,B_phi_Lap=nd.construct_matrix(op='laplacian',
                                                    dirichlet_nodes=j_dirichlet_nodes,
                                                    zero_tangential_nodes=j_tan_groups)
            Mblocks.append( [None,M_phi_Lap] )
            Bblocks.append( B_phi_Lap )
        if 1:
            # PHI-PSI relationship
            # When full dirichlet is used, this doesn't help, but if
            # just zero-tangential-gradient is used, this is necessary.
            Mdx,Bdx=nd.construct_matrix(op='dx')
            Mdy,Bdy=nd.construct_matrix(op='dy')
            Mblocks.append( [Mdy,-Mdx] )
            Mblocks.append( [Mdx, Mdy] )
            Bblocks.append( np.zeros(Mdx.shape[1]) )
            Bblocks.append( np.zeros(Mdx.shape[1]) )

        bigM=sparse.bmat( Mblocks )
        rhs=np.concatenate( Bblocks )

        psi_phi,*rest=sparse.linalg.lsqr(bigM,rhs)
        self.psi=psi_phi[:gtri.Nnodes()]
        self.phi=psi_phi[gtri.Nnodes():]

    def plot_psi_phi(self,num=4,thinning=2):
        plt.figure(num).clf()
        fig,ax=plt.subplots(num=num)

        di,dj=np.nanmax(self.gen.nodes['ij'],axis=0) - np.nanmin(self.gen.nodes['ij'],axis=0)

        self.g_int.plot_edges(color='k',lw=0.5,alpha=0.2)
        cset_psi=self.g_int.contour_node_values(self.psi,int(di/thinning),
                                                linewidths=1.5,linestyles='solid',colors='orange',
                                                ax=ax)
        cset_phi=self.g_int.contour_node_values(self.phi,int(dj/thinning),
                                                linewidths=1.5,linestyles='solid',colors='blue',
                                                ax=ax)
        ax.axis('tight')
        ax.axis('equal')

        ax.clabel(cset_psi, fmt="i=%g", fontsize=10, inline=False, use_clabeltext=True)
        ax.clabel(cset_phi, fmt="j=%g", fontsize=10, inline=False, use_clabeltext=True)
        
    def adjust_by_psi_phi(self,g,update=True,src='ij'):
        """
        Move internal nodes of g according to phi and psi fields

        update: if True, actually update g, otherwise return the new values

        g: The grid to be adjusted
        src: the ij coordinate field in self.gen to use.  Note that this needs to be
          compatible with the ij coordinate field used to create g.
        """
        gtri=self.g_int # where psi/phi are defined
        gen=self.gen # generating grid with the target ij

        # when the intermediate grid and gtri were the same:
        #g=self.g_final=self.g_int.copy()
        # When we always used the same target:
        #g=self.g_final=self.create_intermediate_grid(src='ij',coordinates='ij')
        # Now it's passed in

        # Check to be sure that src and g['ij'] are approximately compatible.
        assert np.allclose( g.nodes['ij'].min(), self.gen.nodes[src].min() )
        assert np.allclose( g.nodes['ij'].max(), self.gen.nodes[src].max() )
        
        for coord in [0,1]: # i,j
            gen_valid=(~gen.nodes['deleted'])&(gen.nodes[src+'_fixed'][:,coord])
            # subset of gtri nodes that map to fixed gen nodes
            gen_to_gtri_nodes=[gtri.select_nodes_nearest(x)
                               for x in gen.nodes['x'][gen_valid]]

            # i or j coord:
            all_coord=gen.nodes[src][gen_valid,coord]
            if coord==0:
                all_field=self.psi[gen_to_gtri_nodes]
            else:
                all_field=self.phi[gen_to_gtri_nodes]

            # Build the 1-D mapping of i/j to psi/phi
            # [ {i or j value}, {mean of psi or phi at that i/j value} ]
            coord_to_field=np.array( [ [k,np.mean(all_field[elts])]
                                       for k,elts in utils.enumerate_groups(all_coord)] )
            if coord==0:
                i_psi=coord_to_field
            else:
                j_phi=coord_to_field

        # the mapping isn't necessarily monotonic at this point, but it
        # needs to be..  so force it.
        # enumerate_groups will put k in order, but not the field values
        i_psi[:,1] = np.sort(i_psi[:,1])
        j_phi[:,1] = np.sort(j_phi[:,1])[::-1]

        # Calculate the psi/phi values on the nodes of the target grid
        # (which happens to be the same grid as where the psi/phi fields were
        #  calculated)
        g_psi=np.interp( g.nodes['ij'][:,0],
                         i_psi[:,0],i_psi[:,1])
        g_phi=np.interp( g.nodes['ij'][:,1],
                         j_phi[:,0], j_phi[:,1])

        # Use gtri to go from phi/psi to x,y
        # I think this is where it goes askew.
        # This maps {psi,phi} space onto {x,y} space.
        # But psi,phi is close to rectilinear, and defined on a rectilinear
        # grid.  Whenever some g_psi or g_phi is close to the boundary,
        # the Delaunay triangulation is going to make things difficult.
        interp_xy=utils.LinearNDExtrapolator( np.c_[self.psi,self.phi],
                                              gtri.nodes['x'],
                                              #eps=0.5 ,
                                              eps=None)
        # Save all the pieces for debugging:
        self.interp_xy=interp_xy
        self.interp_domain=np.c_[self.psi,self.phi]
        self.interp_image=gtri.nodes['x']
        self.interp_tgt=np.c_[g_psi,g_phi]
        
        new_xy=interp_xy( np.c_[g_psi,g_phi] )

        if update:
            g.nodes['x']=new_xy
            g.refresh_metadata()
        else:
            return new_xy

    def plot_result(self,num=5):
        plt.figure(num).clf()
        self.g_final.plot_edges()
        plt.axis('equal')

    def remap_ij(self,g,src='ij'):
        """
        g: grid with a nodes['ij'] field
        src: a differently scaled 'ij' field on self.gen

        returns an array like g.node['ij'], but mapped to self.gen.nodes[src].

        In particular, this is useful for calculating what generating ij values
        would be on a nominal resolution grid (i.e. where the grid nodes and edges
        are uniform in IJ space).
        """
        # The nodes of g are defined on IJ, and I want
        # to map those IJ to ij in a local way. Local in the sense that 
        # I may map to different i in different parts of the domain.

        IJ_in=g.nodes['ij'] # g may be generated in IJ space, but the field is still 'ij'

        # Make a hash to ease navigation
        IJ_to_n={ tuple(IJ_in[n]):n 
                  for n in g.valid_node_iter() }
        ij_out=np.zeros_like(IJ_in)*np.nan

        for coord in [0,1]: # psi/i,  phi/j
            fixed=np.nonzero( self.gen.nodes[src+'_fixed'][:,coord] )[0]
            for gen_n in fixed:
                val=self.gen.nodes[src][gen_n,coord]
                # match that with a node in g
                n=g.select_nodes_nearest( self.gen.nodes['x'][gen_n] )
                # Should be a very good match.  Could also search
                # based on IJ, and get an exact match
                assert np.allclose( g.nodes['x'][n], self.gen.nodes['x'][gen_n] ), "did not find a good match g~gen, based on x"
                ij_out[n,coord]=val

                # Traverse in IJ space (i.e. along g grid lines)
                for incr in [1,-1]:
                    IJ_trav=IJ_in[n].copy()
                    while True:
                        # 1-coord, as we want to move along the constant contour of coord.
                        IJ_trav[1-coord]+=incr
                        if tuple(IJ_trav) in IJ_to_n:
                            n_trav=IJ_to_n[tuple(IJ_trav)]
                            if np.isfinite( ij_out[n_trav,coord] ):
                                assert ij_out[n_trav,coord]==val,"Encountered incompatible IJ"
                            else:
                                ij_out[n_trav,coord]=val
                        else:
                            break

            # just one coordinte at a time
            valid=np.isfinite( ij_out[:,coord] )
            interp_IJ_to_ij=utils.LinearNDExtrapolator(IJ_in[valid,:], ij_out[valid,coord])
            ij_out[~valid,coord] = interp_IJ_to_ij(IJ_in[~valid,:])
        return ij_out
        



