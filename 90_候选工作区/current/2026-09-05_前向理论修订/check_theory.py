"""Independent algebraic checks, not a free-boundary or device simulation."""
import math
import random
import re
import unittest
from pathlib import Path

def trap(v, h):
    return h*(sum(v)-.5*(v[0]+v[-1]))

class TheoryChecks(unittest.TestCase):
    def test_zero_net_volume_with_internal_flow(self):
        n=1000; h=1/n
        rates=[math.sin(2*math.pi*i*h) for i in range(n+1)]
        q=[-(1-math.cos(2*math.pi*i*h))/(2*math.pi) for i in range(n+1)]
        self.assertAlmostEqual(trap(rates,h),0.,places=12)
        self.assertAlmostEqual(max(map(abs,q)),1/math.pi)
        self.assertAlmostEqual(abs(trap(q,h)),1/(2*math.pi))
    def test_local_continuity(self):
        z=.27; h=1e-6
        q=lambda x: -(1-math.cos(2*math.pi*x))/(2*math.pi)
        self.assertAlmostEqual((q(z+h)-q(z-h))/(2*h)+math.sin(2*math.pi*z),0.,places=8)
    def test_moving_closed_end_flux(self):
        area, speed=.03,.4
        qminus=area*speed
        self.assertAlmostEqual(qminus-area*speed,0.)
    def test_pressure_against_atmosphere_not_endpoint_only(self):
        p=lambda z:(math.cos(2*math.pi*z)-1)/(2*math.pi)
        self.assertAlmostEqual(p(0),0.)
        self.assertAlmostEqual(p(1),0.)
        self.assertGreater(abs(p(.5)),.3)
    def test_triangle_pressure_bound(self):
        rng=random.Random(13)
        for unused in range(20):
            rate=[rng.uniform(-2,2) for i in range(60)]
            h=.01; q0=.1; q=[]; running=q0
            for a in rate:
                running-=h*a; q.append(running)
            qub=abs(q0)+h*sum(map(abs,rate))
            self.assertLessEqual(max(map(abs,q)),qub+1e-14)
            resistance=[rng.uniform(1,5) for i in q]
            pressure=[h*sum(r*v for r,v in zip(resistance[j:],q[j:])) for j in range(len(q))]
            bound=h*sum(r*abs(v) for r,v in zip(resistance,q))
            self.assertLessEqual(max(map(abs,pressure)),bound+1e-14)
            self.assertLessEqual(bound,h*len(q)*max(resistance)*qub+1e-14)
    def test_poisseuille_counterexample(self):
        mu,L,r=1.8e-5,.05,1e-4
        a=math.pi*r*r*.1/.01
        dp=4*mu*a*L*L/(math.pi**2*r**4)
        self.assertAlmostEqual(dp,57.29577951308232)
    def test_interface_ramp_memory(self):
        tau,v,f=.2,1.,3.
        up,down=f-tau*v,f+tau*v
        self.assertAlmostEqual(tau*v+up,f)
        self.assertAlmostEqual(-tau*v+down,f)
        self.assertAlmostEqual(down-up,2*tau*v)
    def test_positive_mobility_volume_and_energy(self):
        F=[1.,3.]; K=[[2.,.5],[.5,1.]]
        mul=lambda x:[sum(a*b for a,b in zip(row,x)) for row in K]
        lam=sum(mul(F))/sum(mul([1.,1.]))
        residual=[x-lam for x in F]; v=[-x for x in mul(residual)]
        self.assertAlmostEqual(sum(v),0.)
        self.assertLessEqual(sum(a*b for a,b in zip(F,v)),0.)
    def test_bubble_multiplier_feedback(self):
        ao,ab,fo,fb,m=4.,1.,2.,6.,.3
        lam=lambda f:(ao*fo+ab*f)/(ao+ab)
        delta=lam(fb+.5)-lam(fb)
        self.assertAlmostEqual(delta,ab/(ao+ab)*.5)
        self.assertGreater(m*delta,0.)
    def test_document_invariants(self):
        s=Path('01_最终理论/01_前向预测理论.md').read_text()
        tags=[v.split('}',1)[0] for v in s.split(chr(92)+'tag{')[1:]]
        self.assertGreater(len(tags),80)
        self.assertEqual(len(tags),len(set(tags)))
        self.assertEqual(sum(line.strip()=='$$' for line in s.splitlines())%2,0)
        self.assertIn('整个界面的定量预测统一终止',s)
        self.assertIn('局部连续性',s)
        self.assertIn('开放头空间与受限气道须区分',s)
        self.assertNotIn('仍有强且可重复历史差异',s)
        self.assertNotIn('接触、合并、夹断和再连通均由同一水平集广义解继续',s)
if __name__=='__main__': unittest.main(verbosity=2)
