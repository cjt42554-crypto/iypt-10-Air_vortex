#!/usr/bin/env python3
"""Independent synthetic checks for S1--S18 and T1--T14.

Not a production free-boundary or device solver. All inputs are nondimensional
synthetic references unless a test explicitly states otherwise. No clipping,
changing physical parameters during refinement, or hidden relaxation is used.
Run: python verify_pressure_film.py [--output RESULTS_JSON]
Requires numpy, scipy, sympy. The script writes actual results and returns nonzero
on any failure. Random seed is fixed; integration output is not the step grid.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
import re
import sys
import time
import unittest
from functools import lru_cache
from pathlib import Path
import numpy as np
import scipy
from scipy.integrate import quad, solve_ivp
from scipy.linalg import eigh
from scipy.special import jn_zeros
from scipy.sparse import csr_matrix, diags
import sympy as sp

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA: dict[str, object] = {}
SEED = 20260905
r, z, y = sp.symbols('r z y', positive=True)

@lru_cache(None)
def manufactured():
    # Closed no-slip unit cylinder; NOT the free-surface vessel boundary.
    psi = r**2*(1-r**2)**2*z**2*(1-z)**2
    vr = sp.expand(-sp.diff(psi,z)/r)
    vz = sp.expand(sp.diff(psi,r)/r)
    def lap(f): return sp.diff(f,r,2)+sp.diff(f,r)/r+sp.diff(f,z,2)
    Lr = sp.expand(lap(vr)-vr/r**2)
    Lz = sp.expand(lap(vz))
    pressure = sp.integrate(Lz,z)+500*r**2 # rho=mu=1; hydrostatic part removed
    force = sp.expand(sp.diff(pressure,r)-Lr)
    return vr,vz,pressure,force,Lr,Lz

def mobility(k: float, depth: float=1., mu: float=1.) -> float:
    if k < 0 or depth <= 0 or mu <= 0: raise ValueError('invalid parameters')
    if k == 0: return 0.
    x=k*depth
    if x < 1e-3:
        # Stable numerator; no subtraction of nearly equal sinh(2x) and 2x.
        num=(4/3)*x**3+(4/15)*x**5+(8/315)*x**7
        den=2*mu*k*(np.cosh(2*x)+2*x*x+1)
        return float(num/den)
    if x > 100:
        e=np.exp(-2*x)
        return float((1-e*e-4*x*e)/(2*mu*k*(1+e*e+(4*x*x+2)*e)))
    return float((np.sinh(2*x)-2*x)/(2*mu*k*(np.cosh(2*x)+2*x*x+1)))

def independent_mobility(k: float, depth: float, mu: float) -> float:
    # Solve the four BCs using a different basis than the closed formula.
    zz=sp.symbols('zz', real=True)
    basis=[sp.exp(k*zz),sp.exp(-k*zz),zz*sp.exp(k*zz),zz*sp.exp(-k*zz)]
    mat=np.array([[float(b.subs(zz,0)) for b in basis],
        [float(sp.diff(b,zz).subs(zz,0)) for b in basis],
        [float((sp.diff(b,zz,2)+k*k*b).subs(zz,depth)) for b in basis],
        [float((-mu*sp.diff(b,zz,3)/(k*k)+3*mu*sp.diff(b,zz)).subs(zz,depth)) for b in basis]])
    coef=np.linalg.solve(mat,np.array([0.,0.,0.,1.]))
    return float(coef @ np.array([float(b.subs(zz,depth)) for b in basis]))

def wet(h, pstar=.1, hstar=.6):
    h=np.asarray(h)
    if np.any(h<=0): raise ValueError('nonpositive film; no clipping allowed')
    q=hstar/h
    return pstar*hstar*(q**5/5-q**2/2)

def wet1(h,pstar=.1,hstar=.6):
    h=np.asarray(h)
    if np.any(h<=0): raise ValueError('nonpositive film; no clipping allowed')
    q=hstar/h
    return pstar*(q**3-q**6)

def wet2(h,pstar=.1,hstar=.6):
    q=hstar/np.asarray(h)
    return pstar/hstar*(-3*q**4+6*q**7)

class Film:
    """Closed radial FV reference. Exact semidiscrete mass/gradient structure."""
    def __init__(self,n: int,sigma=.03,gravity=.2,pstar=.1,hstar=.6,slip=.02,drive=.2):
        self.n=n; self.dx=1/n; self.rf=np.linspace(0,1,n+1)
        self.r=(self.rf[:-1]+self.rf[1:])/2
        self.w=(self.rf[1:]**2-self.rf[:-1]**2)/2
        self.sigma=sigma;self.g=gravity;self.ps=pstar;self.hs=hstar
        self.slip=slip;self.psi=drive*self.r**2
        L=np.zeros((n,n))
        for j in range(1,n):
            c=self.rf[j]/self.dx
            L[j-1,j]+=c/self.w[j-1];L[j-1,j-1]-=c/self.w[j-1]
            L[j,j-1]+=c/self.w[j];L[j,j]-=c/self.w[j]
        self.L=csr_matrix(L)
        self.sparsity=diags([np.ones(n-abs(k)) for k in range(-2,3)],range(-2,3),shape=(n,n),format='csr')
    def mu(self,h):return -self.sigma*(self.L@h)+self.g*h+wet1(h,self.ps,self.hs)-self.psi
    def flux(self,h):
        chem=self.mu(h);mob=h**3/3+self.slip*h**2
        q=np.zeros(self.n+1)
        q[1:-1]=-.5*(mob[:-1]+mob[1:])*np.diff(chem)/self.dx
        return q
    def rhs(self,t,h):
        q=self.flux(h)
        return -np.diff(self.rf*q)/self.w
    def energy(self,h):
        surf=.5*self.sigma*np.sum(self.rf[1:-1]*np.diff(h)**2/self.dx)
        return float(2*np.pi*(surf+np.dot(self.w,.5*self.g*h*h+wet(h,self.ps,self.hs)-self.psi*h)))
    def diss(self,h):
        chem=self.mu(h);mob=h**3/3+self.slip*h**2
        return float(2*np.pi*np.sum(self.rf[1:-1]*.5*(mob[:-1]+mob[1:])*np.diff(chem)**2/self.dx))

class PressureTests(unittest.TestCase):
    def test_01_mean_square(self):
        t=np.arange(4096)*2*np.pi/4096;u=1+np.cos(t)
        self.assertAlmostEqual(float(np.mean(u*u)),1.5,places=13)
        DATA['mean_square_vs_squared_mean']=[float(np.mean(u*u)),float(np.mean(u)**2)]
    def test_02_isotropic_covariance_divergence(self):
        k=r**2*z+z**3
        divr=sp.diff(r*k,r)/r-k/r
        self.assertEqual(sp.simplify(divr-sp.diff(k,r)),0)
        self.assertEqual(sp.diff(k,z),r*r+3*z*z)
    def test_03_pressure_compatibility_counterexample(self):
        f=r*(1+z)**2
        self.assertNotEqual(sp.diff(f,z),0)
        # p_r=f, p_z=-g cannot be mixed derivatives of one pressure.
    def test_04_rigid_swirl_pressure(self):
        omg,rho,g=sp.symbols('Omega rho g',positive=True)
        p=rho*omg**2*r*r/2-rho*g*z
        self.assertEqual(sp.diff(p,r)-rho*(omg*r)**2/r,0)
        self.assertEqual(sp.diff(p,z)+rho*g,0)
    def test_05_manufactured_divergence(self):
        vr,vz,*_=manufactured()
        self.assertEqual(sp.simplify(sp.diff(r*vr,r)/r+sp.diff(vz,z)),0)
    def test_06_manufactured_pressure_and_curl(self):
        vr,vz,p,f,Lr,Lz=manufactured()
        self.assertEqual(sp.simplify(-sp.diff(p,r)+Lr+f),0)
        self.assertEqual(sp.simplify(-sp.diff(p,z)+Lz),0)
        self.assertEqual(sp.simplify(sp.diff(Lr+f,z)-sp.diff(Lz,r)),0)
        self.assertNotEqual(sp.diff(f,z),0)
        rr,zz=np.meshgrid(np.linspace(.001,1,60),np.linspace(0,1,70))
        fval=sp.lambdify((r,z),f*r,'numpy')(rr,zz)
        self.assertGreater(float(np.min(fval)),0)
        DATA['manufactured_swirl_squared_min']=float(np.min(fval))
    def test_07_manufactured_no_slip(self):
        vr,vz,*_=manufactured()
        for b in [vr,vz]:
            self.assertEqual(sp.simplify(b.subs(r,1)),0)
            self.assertEqual(sp.simplify(b.subs(z,0)),0)
            self.assertEqual(sp.simplify(b.subs(z,1)),0)
    def test_08_manufactured_work_dissipation(self):
        vr,vz,p,f,*_=manufactured()
        drr=sp.diff(vr,r);dtt=vr/r;dzz=sp.diff(vz,z)
        drz=(sp.diff(vr,z)+sp.diff(vz,r))/2
        dsq=sp.expand(drr*drr+dtt*dtt+dzz*dzz+2*drz*drz)
        def integ(e):return sp.integrate(sp.integrate(sp.expand(e*r),(r,0,1)),(z,0,1))
        power=integ(f*vr);diss=2*integ(dsq)
        self.assertEqual(sp.simplify(power-diss),0)
        self.assertGreater(float(diss),0)
        DATA['manufactured_power_and_dissipation_without_2pi']=[str(power),str(diss)]
    def test_09_isotropic_boundary_traction_cancels(self):
        pi,k=sp.symbols('pi k'); n=sp.Matrix([sp.Rational(3,5),0,sp.Rational(4,5)])
        T=-pi*sp.eye(3)-k*sp.eye(3)
        self.assertEqual(sp.simplify((n.T*T*n)[0]+pi+k),0)
    def test_10_psd_covariance_not_passivity(self):
        D=np.diag([1.,-1.,0.]);Rp=np.diag([2.,0.,0.]);Rm=np.diag([0.,2.,0.])
        self.assertGreater(float(np.sum(Rp*D)),0);self.assertLess(float(np.sum(Rm*D)),0)

class MobilityTests(unittest.TestCase):
    def test_11_independent_stokes_boundary_problem(self):
        vals=[]
        for k,d,mu in [(.3,.8,.7),(.8,1.4,2.),(2.,.5,1.),(3.,1.5,.4)]:
            a=mobility(k,d,mu);b=independent_mobility(k,d,mu)
            err=abs(a-b)/a; self.assertLess(err,1e-9);vals.append(err)
        DATA['mobility_boundary_value_relative_errors']=vals
    def test_12_shallow_limit(self):
        vals=[]
        for k in [1e-2,1e-3,1e-4]:
            err=abs(mobility(k)/(k*k/3)-1);vals.append(err)
        self.assertLess(vals[-1],3e-8);self.assertGreater(vals[0],vals[1]);DATA['shallow_limit_errors']=vals
    def test_13_deep_limit(self):
        self.assertLess(abs(mobility(20)*40-1),1e-12)
    def test_14_zero_mode_and_positivity(self):
        self.assertEqual(mobility(0),0)
        self.assertTrue(all(mobility(k)>0 for k in np.logspace(-6,3,80)))
    def test_15_forced_mode_phase(self):
        M=mobility(1.2,.7,.4);gamma=M*(.3+.8*1.2**2);freq=.9
        def exact(t):return M*(gamma*np.cos(freq*t)+freq*np.sin(freq*t))/(gamma*gamma+freq*freq)
        sol=solve_ivp(lambda t,a:[-gamma*a[0]+M*np.cos(freq*t)],(0,10),[exact(0)],rtol=1e-11,atol=1e-13,dense_output=True)
        t=np.linspace(0,10,301);err=np.max(abs(sol.sol(t)[0]-exact(t)))
        self.assertTrue(sol.success);self.assertLess(err,1e-10);DATA['forced_stokes_mode_max_error']=float(err)

class FilmTests(unittest.TestCase):
    def test_16_slip_flux_from_velocity(self):
        h,b,mu,B=sp.symbols('h b mu B',positive=True)
        u=B/mu*(b*h+h*y-y*y/2)
        self.assertEqual(sp.diff(u,y).subs(y,h),0)
        self.assertEqual(sp.simplify(u.subs(y,0)-b*sp.diff(u,y).subs(y,0)),0)
        self.assertEqual(sp.simplify(sp.integrate(u,(y,0,h))-B*(h**3/3+b*h*h)/mu),0)
    def test_17_weighted_swirl_against_bvp(self):
        h=1.3;b=.11;mu=.7;rho=.9;rad=.8;pressure_gradient=.4
        # Independent polynomial integration of radial momentum with a
        # nonuniform azimuthal profile whose free-surface derivative is zero.
        U=.6+.4*(2*y/h-(y/h)**2)
        B=rho*U**2/rad-pressure_gradient
        s=sp.symbols('s',real=True)
        uprime=sp.integrate(B.subs(y,s),(s,y,h))/mu
        u=b*uprime.subs(y,0)+sp.integrate(uprime.subs(y,s),(s,0,y))
        direct=float(sp.integrate(u,(y,0,h)))
        weighted=float(sp.integrate((b*h+h*y-y*y/2)*B/mu,(y,0,h)))
        self.assertAlmostEqual(direct,weighted,places=11)
        Ubar=float(sp.integrate(U,(y,0,h))/h)
        shortcut=(h**3/3+b*h*h)/mu*(rho*Ubar**2/rad-pressure_gradient)
        self.assertGreater(abs(direct-shortcut),1e-3)
        DATA['weighted_swirl_flux']={'direct':direct,'weighted':weighted,'squared_depth_mean_shortcut':shortcut}
    def test_18_corotation_reduction(self):
        h,b,O,rho,mu=sp.symbols('h b O rho mu',positive=True)
        j=rho/(mu*r)*sp.integrate((b*h+h*y-y*y/2)*(O*r)**2,(y,0,h))
        self.assertEqual(sp.simplify(j-(h**3/3+b*h*h)/mu*rho*O*O*r),0)
    def test_19_wetting_derivatives(self):
        h,hs,P=sp.symbols('h hs P',positive=True)
        W=P*hs*((hs/h)**5/5-(hs/h)**2/2)
        W1=P*((hs/h)**3-(hs/h)**6)
        self.assertEqual(sp.simplify(sp.diff(W,h)-W1),0)
        self.assertEqual(sp.simplify(sp.diff(W1,h).subs(h,hs)-3*P/hs),0)
        self.assertEqual(sp.simplify(W.subs(h,hs)+sp.Rational(3,10)*P*hs),0)
    def test_20_contact_angle_first_integral(self):
        P=.8;hs=.3;sigma=1.1
        integral=quad(lambda h:float(wet1(h,P,hs)),hs,np.inf,epsabs=1e-12)[0]
        self.assertAlmostEqual(integral,.3*P*hs,places=11)
        self.assertAlmostEqual(2*integral/sigma,3*P*hs/(5*sigma),places=11)
    def test_21_unsteady_spin_thinning(self):
        O=lambda t:.7+.2*np.sin(t);rho=1.1;mu=.8;h0=.9
        sol=solve_ivp(lambda t,h:[-2*rho*O(t)**2*h[0]**3/(3*mu)],(0,6),[h0],method="DOP853",rtol=2e-13,atol=2e-15,dense_output=True)
        errs=[]
        for t in np.linspace(0,6,21):
            ex=1/np.sqrt(h0**-2+4*rho/(3*mu)*quad(lambda s:O(s)**2,0,t,epsabs=1e-12)[0]);errs.append(abs(sol.sol(t)[0]-ex))
        self.assertTrue(sol.success);self.assertLess(max(errs),1e-10);DATA['unsteady_spin_thinning_error']=float(max(errs))
    def test_22_slip_accelerates_positive_drainage(self):
        end=[]
        for b in [0.,.1]:
            sol=solve_ivp(lambda t,h:[-2*(h[0]**3/3+b*h[0]**2)],(0,5),[1.],rtol=1e-10,atol=1e-12)
            self.assertTrue(sol.success);self.assertGreater(float(sol.y.min()),0);end.append(float(sol.y[0,-1]))
        self.assertLess(end[1],end[0]);DATA['drainage_final_no_slip_and_slip']=end
    def test_23_radial_mass_and_energy_identity(self):
        f=Film(53);h=1+.03*np.cos(np.pi*f.r)+.008*np.cos(2*np.pi*f.r)
        rhs=f.rhs(0,h);mass=float(f.w@rhs)
        rate=float(2*np.pi*(f.w*f.mu(h))@rhs)
        self.assertLess(abs(mass),1e-10);self.assertLess(abs(rate+f.diss(h)),1e-9)
        DATA['radial_semidiscrete_balance']={'mass_rate':mass,'energy_rate':rate,'dissipation':f.diss(h)}
    def test_24_radial_energy_gradient(self):
        rng=np.random.default_rng(SEED);f=Film(27);h=1+.02*np.cos(np.pi*f.r);v=rng.normal(size=27);v/=np.linalg.norm(v)
        eps=1e-6;fd=(f.energy(h+eps*v)-f.energy(h-eps*v))/(2*eps)
        ex=float(2*np.pi*(f.w*f.mu(h))@v)
        self.assertLess(abs(fd-ex),1e-9)
    def test_25_uniform_closed_film_is_stationary(self):
        f=Film(48,drive=0);h=np.ones(48)
        self.assertLess(float(np.max(abs(f.rhs(0,h)))),1e-9)
    def test_26_nonlinear_closed_film(self):
        f=Film(56);h0=1+.04*np.cos(np.pi*f.r)
        sol=solve_ivp(f.rhs,(0,.5),h0,method='BDF',rtol=2e-9,atol=2e-11,jac_sparsity=f.sparsity,dense_output=True)
        self.assertTrue(sol.success,sol.message)
        ts=np.linspace(0,.5,101);hs=sol.sol(ts);Es=np.array([f.energy(h) for h in hs.T]);mass=f.w@hs
        self.assertGreater(float(np.min(hs)),0)
        self.assertLess(float(np.max(abs(mass-mass[0]))),1e-10)
        self.assertLess(float(np.max(np.diff(Es))),1e-9)
        self.assertLess(Es[-1],Es[0]-1e-5)
        DATA['nonlinear_closed_film']={'cells':f.n,'accepted_steps':len(sol.t)-1,'nfev':sol.nfev,'min_h':float(hs.min()),'mass_drift':float(np.max(abs(mass-mass[0]))),'energy_initial':float(Es[0]),'energy_final':float(Es[-1]),'max_energy_increment':float(np.max(np.diff(Es)))}
    def test_27_axisymmetric_mode_spatial_convergence(self):
        ex=float(jn_zeros(1,1)[0]**2);errs=[];vals=[]
        for n in [32,64,128]:
            f=Film(n);S=-np.sqrt(f.w)[:,None]*f.L.toarray()/np.sqrt(f.w)[None,:]
            val=float(eigh(S,subset_by_index=[1,1],eigvals_only=True)[0]);vals.append(val);errs.append(abs(val-ex))
        orders=np.log2(np.array(errs[:-1])/np.array(errs[1:]))
        self.assertTrue(np.all(orders>1.9));self.assertLess(errs[-1]/ex,1e-4)
        DATA['radial_laplacian_convergence']={'cells':[32,64,128],'continuum_eigenvalue':ex,'values':vals,'errors':errs,'orders':orders.tolist()}
    def test_28_growth_rate_linearization(self):
        f=Film(32,sigma=.001,gravity=0,pstar=.3,hstar=.4,drive=0)
        S=-np.sqrt(f.w)[:,None]*f.L.toarray()/np.sqrt(f.w)[None,:]
        vals,vec=eigh(S,subset_by_index=[1,1]);lam=float(vals[0]);v=vec[:,0]/np.sqrt(f.w);v/=np.max(abs(v))
        hbar=.65;eps=1e-6
        numerical=(f.rhs(0,hbar+eps*v)-f.rhs(0,hbar-eps*v))/(2*eps)
        M=hbar**3/3+f.slip*hbar*hbar;rate=-M*lam*(f.sigma*lam+float(wet2(hbar,f.ps,f.hs)))
        self.assertGreater(rate,0);self.assertLess(float(np.max(abs(numerical-rate*v))),2e-7)
        DATA['discrete_mode_growth_rate']=rate
    def test_29_open_boundary_power(self):
        h=1+sp.Rational(1,5)*r*r+sp.Rational(1,20)*r**4
        sigma=sp.Rational(7,10);g=sp.Rational(1,5);b=sp.Rational(1,10);Psi=r*r/10
        M=h**3/3+b*h*h;chem=-sigma*(sp.diff(r*sp.diff(h,r),r)/r)+g*h-Psi
        q=sp.expand(-M*sp.diff(chem,r));ht=sp.expand(-sp.diff(r*q,r)/r)
        direct=sp.integrate(sp.expand((sigma*sp.diff(h,r)*sp.diff(ht,r)+(g*h-Psi)*ht)*r),(r,0,1))
        diss=sp.integrate(sp.expand(M*sp.diff(chem,r)**2*r),(r,0,1))
        boundary=(sigma*r*sp.diff(h,r)*ht-r*chem*q).subs(r,1)
        self.assertEqual(sp.simplify(direct+diss-boundary),0)
        self.assertNotEqual(boundary,0)
        self.assertEqual(sp.simplify(sp.integrate(ht*r,(r,0,1))+q.subs(r,1)),0)
        DATA['open_boundary_power_without_2pi']={'direct':float(direct),'dissipation':float(diss),'boundary':float(boundary)}
    def test_30_real_rk4_time_refinement(self):
        def rhs(t,h):return -2*(.8+.3*np.sin(t))**2*h**3/3
        T=3.;h0=1.;ex=1/np.sqrt(1+4/3*quad(lambda t:(.8+.3*np.sin(t))**2,0,T,epsabs=1e-13)[0])
        vals=[];errs=[]
        for n in [16,32,64,128]:
            h=h0;dt=T/n
            for j in range(n):
                t=j*dt;k1=rhs(t,h);k2=rhs(t+dt/2,h+dt*k1/2);k3=rhs(t+dt/2,h+dt*k2/2);k4=rhs(t+dt,h+dt*k3);h+=dt*(k1+2*k2+2*k3+k4)/6
            vals.append(h);errs.append(abs(h-ex))
        orders=np.log2(np.array(errs[:-1])/np.array(errs[1:]));self.assertTrue(np.all(orders>3.5))
        DATA['drainage_rk4_refinement']={'steps':[16,32,64,128],'actual_dt':[T/n for n in [16,32,64,128]],'errors':errs,'orders':orders.tolist()}
    def test_31_nonpositive_thickness_rejected(self):
        with self.assertRaises(ValueError):wet1(np.array([1.,0.]))
        with self.assertRaises(ValueError):Film(8).rhs(0,np.zeros(8))

class DocumentationTests(unittest.TestCase):
    def test_32_no_control_characters_and_unique_tags(self):
        text=(ROOT/'01_最终理论/01_前向预测理论.md').read_text()
        self.assertFalse(any(ord(c)<32 and c not in '\n\t' for c in text))
        self.assertEqual(text.count('$$')%2,0)
        tags=re.findall(r'\\tag\{([^}]+)\}',text)
        self.assertEqual(len(tags),len(set(tags)))
        for tag in ['S3','S6','S11','S14','S15','T4','T5','T8','T10a','T10b','T12','T14']:
            self.assertIn(tag,tags)
    def test_33_implementation_boundary_and_history(self):
        for name in ['01_最终理论/02_前向预测理论的数值计算理论.md','01_最终理论/README.md','README.md']:
            t=(ROOT/name).read_text();self.assertIn('SH',t);self.assertIn('TF',t)
            self.assertFalse(any(ord(c)<32 and c not in '\n\t' for c in t))
        self.assertEqual((ROOT/'00_项目管理/交接说明_history.md').stat().st_size,0)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=HERE/'results.json');args=parser.parse_args()
    start=time.monotonic();res=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    data={'source_commit':'2461446290106ae6d49b6922cc152b320e8c3a92','seed':SEED,'environment':{'python':sys.version,'platform':platform.platform(),'numpy':np.__version__,'scipy':scipy.__version__,'sympy':sp.__version__},'scope':'independent synthetic identities, fixed-domain Stokes BVP, radial film and scalar drainage; not full vessel, wetting-line or SH--TF coupling validation','tests':{'run':res.testsRun,'failures':len(res.failures),'errors':len(res.errors),'skipped':len(res.skipped)},'elapsed_seconds':time.monotonic()-start,'metrics':DATA,'theory_sha256':hashlib.sha256((ROOT/'01_最终理论/01_前向预测理论.md').read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');sys.exit(not res.wasSuccessful())
