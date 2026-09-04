from pathlib import Path
import subprocess

p = Path('01_最终理论/01_前向预测理论.md')
assert subprocess.check_output(['git', 'hash-object', str(p)], text=True).strip() == '9034d636a4add45a7fd73dffbfd2cb76b2c0981e'
s = p.read_text()

def rep(old, new):
    global s
    assert s.count(old) == 1, old[:120]
    s = s.replace(old, new)

def section(start, end, body):
    global s
    assert s.count(start) == s.count(end) == 1
    a, b = s.index(start), s.index(end)
    assert a < b
    s = s[:a] + body.rstrip() + '\n\n' + s[b:]

rep('> 结论边界：本文给出闭合、可计算、可证伪的连续模型；参数值与装置级预测仍须由独立实验检验', '> 结论边界：本文给出光滑有效阶段的条件性前向模型；未建立唯一续接的几何奇性、封闭气体出现或等压假设失效后终止整个界面的定量预测。参数值与装置级预测仍须由独立实验检验  \n> 理论修订：2026-09-05；局部气体通量、完整历史记忆及定量终止边界')
rep('接触、合并、夹断和再连通均由同一水平集广义解继续，不另设人工几何重置。', '光滑阶段按同一几何方程演化，不作人工几何重置。接触、合并、夹断或再连通的唯一续接若未由明确解概念建立，按第 4.4、5.3 节在该事件处终止定量预测，不能仅凭水平集表示自动续接。')
rep('- 动态湿润、接触线钉扎、首次接触、连通、断开和再连通均有确定规则；', '- 动态湿润和光滑接触线钉扎有显式本构；首次接触、连通、断开和再连通有观测定义，但其后继演化须满足第 4.4 节的解条件；')
rep('- 给定合法输入后，模型要么给出唯一的经典/广义演化，要么返回一个预先定义的越域或数学失效状态，不允许在运行途中临时补物理规则。', '- 合法输入不单独保证全局唯一演化。只在所声明的局部解存在唯一、物理适用条件满足的时间段内给出预测；首次未建立续接或越域处返回停止状态，不在运行途中补规则。')
rep('式 (F42) 的物理解只取与初始集合 $\\mathcal L_0$ 对应的轴对称、体积守恒、能量耗散的水平集黏性解。光滑解存在时它与经典界面运动相同；发生接触、合并或夹断时，由同一弱解继续，不作从“触及”到“通道”的人工形状重置。'.replace('\\\\', '\\'), '式 (F42) 首先是光滑界面运动的表示。轴对称、体积守恒和能量耗散是相容性要求，不足以单独定义奇性后的唯一解。本文不据此指定任意接触、合并或夹断之后的唯一水平集黏性解，也不作从“触及”到“通道”的人工形状重置。')
rep('若水平集演化发生“fattening”，使零水平集出现有限体积而无法唯一确定液体集合，则模型将其判为“未解析的几何奇性”，并停止物理声称。这个失败状态是模型规则，而不是在求解过程中临时选择某条界面。', r'''若水平集发生 fattening、接触边界不能相容定义，或首次出现尚无唯一续接的几何奇性，则在最后有效时刻终止定量预测。不要求先观测到两条不同解才允许停止。

延拓的充分依据必须另行明确：解的函数/测度空间、奇性处曲率与面积积分、全局体积乘子、接触角滞后的弱边界条件，以及存在唯一性或经指定选择原则的收敛结果。光滑阶段的能量恒等式不能替代这些条件。若选择固定连续正则化，正则化参数和极限选择也须明示；单次离散结果不是该极限。本文尚不提供这一通用续接定理，因此正式默认止于第一个需要该定理的事件。

可从一个已给定、相容的光滑有限气道初态研究其后续光滑演化；这不等于已证明从未接触初态必能唯一穿过首次接触并形成该气道。''')
rep('式 (F35)–(F45) 联立决定接触角、法向界面运动和接触线运动。首次碰到磁体时，不重置 $C$、不凭空建立有限半径气柱；水平集的一侧极限与式 (F44) 自动决定界面是附着、钉扎还是后退。', '式 (F35)–(F45) 在光滑、相容的接触边界上联立规定接触角、法向界面运动和接触线运动。首次碰到磁体时不重置 $C$、不凭空建立有限半径气柱；若新生接触线的一侧极限及唯一续接尚未建立，在该处停止，而不是宣称式 (F44) 自动选出唯一气道。')
rep('因此，首次触及以后仍由同一组方程继续演化。模型的约化来自平均旋流和过阻尼界面动力学，而不是来自禁止拓扑变化。', '这些方程可表达接触前和已有光滑气道两类构型，但表示能力不等于跨越新生接触或拓扑奇性的唯一解保证。只有第 4.4 节的续接条件被建立时，才允许把两段定量演化连接起来。')
rep('6. 水平集能量解继续通过允许的接触和拓扑事件；\n7. 若广义解失去唯一几何语义，则返回预定义失败状态。', '6. 仅在已建立的解类和相容边界内继续；\n7. 首次需要尚未建立的拓扑续接，或任一物理适用条件失效时，返回预定义停止状态。')
section('### 5.3 经典解、广义解和失败状态', '## 6. 贯通前的解析约化与直接阈值', r'''### 5.3 条件唯一性与整个界面的定量终点

给定相容初态，在局部存在唯一的光滑界面/接触线解期间，式 (F35)–(F45) 定义条件前向演化。本文不把形式闭合或能量耗散称为已证明的全局适定性。

令 $\tau_{\rm geom}$ 为首次离开已建立解类或遇到未建立唯一续接的几何事件的时刻，$\tau_g$ 为首次气体等压检查失效或不再可判定的时刻，$\tau_{\rm iso}$ 为首次产生封闭气体的时刻，$\tau_{\rm other}$ 为其他第 11 节适用条件失效的时刻。定量终点为

$$
\tau_{\rm quant}=\min(\tau_{\rm geom},\tau_g,\tau_{\rm iso},\tau_{\rm other}).
\tag{F48a}
$$

空集下确界取 $+\infty$；初态已不满足条件时相应时刻取零。有效预测为 $0\le t<\min(T,\tau_{\rm quant})$；若 $T<\tau_{\rm quant}$，包含终点 $T$。事件前极限可单列记录，不等于事件后的预测。

停止后，可以保存明确标为诊断的正则化几何轨迹，但不能继续报告通道半径、保持时间、再连通或泡径为正式定量结果。若缺少气体压降或解条件的必要资料，状态是未验证，而不是默认通过。

---''')
section('### 7.6 封闭气体分量', '## 8. 可证伪的前向预测', r'''### 7.6 封闭气体分量与全局定量失效

定义非大气连通气体集合

$$
\mathcal G_{\mathrm{iso}}(t)
=\mathcal G(t)\setminus\mathcal G_{\mathrm{atm}}(t).
\tag{F78}
$$

首次出现 $|\mathcal G_{\mathrm{iso}}|>0$ 时，基线缺少每个封闭气体分量的质量和内部压力状态。若初态已含封闭气体，基线从起点即不具有整个界面的定量资格。

该缺口不局限于泡径。把界面分为外部界面与气泡界面，面积为 $A_o,A_b$，并记 $F=\Delta\rho g(z-H)+\sigma\kappa-\rho_l\Phi$。常数迁移率下

$$
\lambda_V=\frac{A_o\overline F_o+A_b\overline F_b}{A_o+A_b}.
$$

固定几何的一阶扰动满足

$$
\delta\lambda_V=\frac{A_b}{A_o+A_b}\delta\overline F_b,
\qquad \delta V_{n,o}=M_\Gamma\delta\lambda_V.
\tag{F78a}
$$

因此气泡压力误差可经公共乘子反馈至外部界面。首次封闭气体形成后，整个界面的定量预测统一终止；继续计算只能作诊断，不只是取消泡径/泡压标签。

需要定量延拓时，须另建含分量气体质量—压力状态、合并/分裂规则及明确解选择的模型。可以先研究轴对称扩展；是否必须三维由非轴对称误差决定，而不是仅因需要气体压力就预先规定。

---''')
old = '| FP-14 | 降速穿过通道维持区 | 因 $C$ 滞后和接触线钉扎，断开不必与升速形成同一时刻 | 上/下扫同步数据 | 独立测得 $\\tau_C=0$、无滞后，仍有强且可重复历史差异 |'
rep(old, '| FP-14 | 在定量有效区内比较相同实际棒速的上/下扫 | 环量、有限界面松弛、接触线及初态均可造成记忆 | 上/下扫同步数据及各松弛时间 | 历史差异超过独立标定的全部记忆和允许多稳态所能解释的联合不确定度；仅排除环量延迟与接触角滞后不构成证伪 |')
rep('| FP-15 | 首次出现封闭气体分量 | 模型对其只作轴对称定性输出，不作定量泡径声明 | 三维高速视频 | 不适用；这是能力边界而非待命中数值 |', '| FP-15 | 首次出现封闭气体分量 | 整个界面的定量预测终止，后续几何仅供诊断 | 连通性及多视角视频 | 不适用；这是能力边界，不是装置数值预测 |')
rep('预测 FP-07 至 FP-10 直接补上旧理论在首次触及后的规则空白；它们仍是待实验检验的模型输出，不是已经证实的现象定律。', r'''FP-07 至 FP-10 是适用域和解条件内的本构预测，不构成首次接触后唯一续接的证明。FP-14 不能忽略界面自身的松弛。单模态示例

$$
\tau_\Gamma\dot y+y=f(t),\qquad \tau_\Gamma>0
$$

在同一驱动值处，对斜率为 $\pm v$ 的稳态斜坡响应有 $y_\downarrow-y_\uparrow=2\tau_\Gamma v$。即使环量瞬时响应且无接触角滞后，仍可有有限速率记忆。准静态证伪还须控制所有相关松弛时间、等待初态暂态消失，并处理模型允许的多稳态。''')
marker = '### 9.4 允许的参数角色'
rep(marker, r'''常数迁移率的检验应比较各激发尺度的幅值、衰减时间与驱动相位，并在留出尺度上使用同一参数及联合不确定度。缓慢驱动不是强阻尼的充分条件。若出现稳定的尺度相关残差，可另建自伴正迁移算子 $K$ 的候选扩展；不能只把标量 $M_\Gamma$ 换掉而保留旧体积乘子。以界面面积内积记 $\langle\cdot,\cdot\rangle$，相容结构为

$$
V_n=K(\lambda 1-F),\qquad
\lambda=\frac{\langle1,KF\rangle}{\langle1,K1\rangle},
\qquad \langle1,K1\rangle>0.
\tag{F80a}
$$

在固定界面瞬时几何与相容边界下，$\langle1,V_n\rangle=0$，主体形状耗散为 $-\langle F-\lambda1,K(F-\lambda1)\rangle\le0$。这只是扩展约束，不是式 (F0a) 的新增闭合，也不是非线性全局定理。只有相位残差确实要求惯性时，才研究额外惯性模态。

''' + marker)
section('### 11.3 气体等压近似的事后检查', '### 11.4 硬失败和降级状态', r'''### 11.3 由局部连续性检查气体等压近似

本节是气体等压近似的条件性诊断，不把净通道容积变化当作内部流量上界。取单一、无分支、细长且近圆截面的气道 $z\in[z_-(t),z_+(t)]$，面积 $\mathcal A(z,t)=\pi r(z,t)^2>0$，上端接大气。气体近似不可压、无侧向质量源时

$$
\partial_t\mathcal A+\partial_zQ=0,
\qquad
Q_-(t)=\mathcal A(z_-,t)\dot z_-+Q_{\rm rel,-}(t),
$$

$$
Q(z,t)=Q_-(t)-\int_{z_-}^{z}\partial_t\mathcal A(\zeta,t)\,\mathrm d\zeta.
\tag{F86}
$$

$Q_{\rm rel,-}$ 是穿过下端移动截面的相对体积通量；封闭随动下端取零，固定不透气底端进一步有 $Q_-=0$。面积导数取固定空间坐标，不能与移动积分端点的导数混同。上端通量由同一连续性和储库边界确定；不能把两端流量与全部面积变化率任意独立指定。

在气体准稳态黏性阻力占优、轴向气体体力可忽略、且所选圆管壁面阻力近似适用时，取 $p_g(z_+)=p_{atm}$，有

$$
p_g(z)-p_{atm}
=\int_z^{z_+}\frac{8\mu_gQ(\zeta)}{\pi r(\zeta)^4}\,\mathrm d\zeta,
\qquad
\Delta p_g=\sup_z|p_g(z)-p_{atm}|.
\tag{F87}
$$

该圆管阻力本身是附加近似，移动气液界面的切向应力条件、入口损失、强锥度、惯性或可压缩性不满足时，不能把它称为真实气流的严格上界。**在上述一维阻力模型内**，令 $L_{\rm ch}=z_+-z_-$，可用

$$
Q_{\rm ub}=|Q_-|+\int_{z_-}^{z_+}|\partial_t\mathcal A|\,\mathrm dz,
$$

$$
\Delta p_g\le
\int_{z_-}^{z_+}\frac{8\mu_g|Q|}{\pi r^4}\,\mathrm dz
\le\frac{8\mu_g L_{\rm ch}Q_{\rm ub}}{\pi r_{\min}^4}
=: \Delta p_{g,\rm ub}.
\tag{F87a}
$$

若只知道总容积变化，不能求出这个上界；资料不足时返回未验证，不能返回零压降。总容积不变仍可有内部气流。例如固定下端封闭、$\partial_t\mathcal A=a\sin(2\pi z/L)$ 时

$$
\dot V_g=0,\qquad
Q(z)=-\frac{aL}{2\pi}[1-\cos(2\pi z/L)]\ne0.
\tag{F87b}
$$

均匀瞬时半径 $r_0$ 下，两端压差幅值为 $4\mu_gaL^2/(\pi^2r_0^4)$（取 $a>0$），不是零。还须使用相对大气的最大沿程压差：带变号流量的两端压差也可能抵消。

保留整体压力尺度

$$
P_{\rm ref}=\max(\sigma/r_{\min},\Delta\rho gL_{\rm ch},\rho_l\Delta\Phi),
\qquad
\epsilon_g=\frac{\Delta p_{g,\rm ub}}{P_{\rm ref}}.
\tag{F88}
$$

这里 $\Delta\Phi$ 为气道邻域液相离心势的最大—最小差。$\epsilon_g\le\epsilon_{g,\max}$ 只是对上述近似模型的筛查，不能仅凭最大压力尺度掩盖局部近抵消驱动力；还须检查压差对目标形状、颈半径和事件时刻的预定误差预算。密度变化、气体惯性及声学响应也必须可忽略，否则采用含气体压力/质量状态的其他模型。

$r_{\min}=0$、多通道汇分流、端部条件未知或所需面积时导数不存在时，本诊断未定义或不适用；不能把 $0/0$ 设为零，也不能声称压降必然按 $r_{\min}^{-4}$ 发散，因为流量本身也可能趋零。此时按第 5.3 节结束定量资格。闭合压力适用性不能用增加计算精度代替。''')
rep('| 气体等压闭合失效 | $\\epsilon_g$ 超限 | 通道几何仅定性；需改用含气相压力的模型 |', '| 气体等压闭合失效或未验证 | 第 11.3 节局部流量/压力检查超限、前提不成立或资料不足 | 整个界面定量预测终止；需补充证据或采用含气体压力的模型 |')
rep('| 仅可作轴对称定性判断 | 出现封闭气泡或三维破碎前兆 | 可继续几何演化，不作泡径或泡压定量声明 |', '| 封闭气体或强三维效应 | 出现封闭气体分量或三维破碎前兆 | 整个界面失去定量资格，后续仅可标为诊断 |')
rep('| 未解析几何奇性 | 水平集零面 fattening 或接触选择非唯一 | 在最后可信时刻停止 |', '| 未建立几何续接 | fattening、非相容新生接触或其他尚无唯一解保证的奇性 | 在最后有效时刻停止；不等待发现两条不同解 |')
rep('在参数独立标定、适用指标通过后，可以声称：', '仅在参数独立标定、适用指标通过且第 5.3 节定量有效时间内，可以声称：')
rep('- 动态湿润规则下的接触线位置、连通、断开和再连通；', '- 光滑相容接触线的位置与已有气道的连通状态；跨奇性的断开/再连通后继结果不自动具有定量资格；')
rep('- 初态、升降速历史和接触线钉扎造成的前向记忆。', '- 初态、环量延迟、界面松弛和接触线钉扎造成的前向记忆。')
section('## 12. 最终模型摘要', '## 13. 原始来源与证据边界', r'''## 12. 最终模型摘要

式 (F0a)–(F0b) 联立规定环量响应、平均旋流势、光滑界面迁移、体积约束和动态接触线。其正式预测是局部解条件与物理适用域内的条件映射，不是任意拓扑变化后的全局唯一性定理。

模型可输出有效区内的 $C(t)$、$\Gamma(t)$、颈半径和连通观测，并记录首达边界。尚未建立唯一续接的首次接触/夹断、封闭气体出现或气体等压检查失效时，整个界面的定量预测终止。事件和气道的定义不是对其必然生成或可唯一延拓的证明。

气体流量按局部连续性及端点条件恢复，不能用净容积变化替代内部流量上界。升降速记忆必须同时考虑环量、界面、接触线、初态和允许的多稳态。常数迁移率仍须跨尺度验证；候选扩展不改变本文已指定的最终方程。

本理论描述方位平均、过阻尼界面包络；装置级定量验证、三维瞬时流动及封闭气体动力学不由形式闭合自动获得。

---''')
p.write_text(s)
d = Path('90_候选工作区/current/2026-09-05_前向理论修订')
d.mkdir(parents=True, exist_ok=True)
(d/'check_theory.py').write_text(r'''"""Independent algebraic checks, not a free-boundary or device simulation."""
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
        tags=re.findall(r'\tag\{([^}]+)\}',s)
        self.assertEqual(len(tags),len(set(tags)))
        self.assertEqual(sum(line.strip()=='$$' for line in s.splitlines())%2,0)
        self.assertIn('整个界面的定量预测统一终止',s)
        self.assertIn('局部连续性',s)
        self.assertNotIn('独立测得 $\tau_C=0$、无滞后，仍有强且可重复历史差异',s)
        self.assertNotIn('接触、合并、夹断和再连通均由同一水平集广义解继续',s)
if __name__=='__main__': unittest.main(verbosity=2)
''')
Path('90_候选工作区/交接说明_current.md').write_text('''# 当前交接：2026-09-05 前向理论修订

## 当前状态

基于 `acb25d12b241ad7efda2ac1bc7ff0011dc9e7be1` 在 main 修订 `01_最终理论/01_前向预测理论.md`。本轮是连续理论的公式、适用域和结论边界修正，不改变生产求解器、网格、参数或既有结果。没有新增实验，没有取得装置定量资格。

## 已完成的理论修改

1. F86–F88 改成局部截面连续性、移动下端相对通量、相对大气的最大沿程压差及条件性三角上界。净容积变化不再充当内部气流上界。圆管阻力、气体惯性/压缩性、入口和界面应力的前提明示；资料不足记未验证，颈半径为零不以 0/0 判通过。
2. FP-14 加入有限界面松弛、初态及允许多稳态的记忆，保留无环量延迟、无角滞后仍可有扫描差异的单模态反例。
3. 第 4.4、4.7、5.1、5.3、7.6、11、12 节及总入口同步收紧：水平集表示、体积守恒、能量耗散不自动选出任意拓扑事件后的唯一解；默认在首次未建立续接处终止。可从相容的光滑已有气道初态研究其演化，但不冒充已证明首次接触后的通道生成。
4. 封闭气体首次出现后，整个界面的定量资格终止；推导公共体积乘子如何把泡压误差反馈到外界面。需要延拓时可先考虑带分量气体状态的轴对称扩展，不预先要求三维。
5. 补充常数迁移率跨尺度幅相检验，以及正迁移算子与体积乘子的相容候选结构。候选结构不进入现有最终耦合方程，没有新增待拟合物理参数。

## 验证与程序差距

可复现检查在 `current/2026-09-05_前向理论修订/check_theory.py`，覆盖净变化抵消、局部连续性、移动端点、沿程压差、通量/压力上界、界面记忆、正迁移率耗散和气泡乘子反馈。实际结果保存在同目录 `validation.txt`。这是代数和文档验证，不是自由边界求解或真实装置验证。

生产程序本轮未接入新的局部气体压差诊断或统一定量终止状态；它若继续输出拓扑/泡后曲线，只能按新理论视为诊断。下一步应先核对并接线这些合同，不要把文稿修改当作程序已经自动遵守新规则。没有重跑完整模拟测试或 Q1R3/D3R1。

## 保留的数值状态与后续

上轮一致 CLS、三维净空通道判据、投影散度控制等修复保持不变。既有 200 项测试（4 项 CUDA 跳过）的记录仍在 `current/2026-09-05_数值审查修复/validation.log`，不是本轮新跑的证据。Q1R3 空间收敛失败和 D3R1 原始散度失败不能据本轮理论修订追认解决。前向与三维后端湿润闭合仍不同，优先在共同的未接触、近平衡工况比较。

先实现新判读合同，再检验目标工况下的局部气流、跨尺度界面响应和功率/相位残差；无明确失配前不扩展完整气相或惯性自由边界模型。`交接说明_history.md` 保持零字节；Git 历史、原始结果及冻结证据不改写。
''')
Path('90_候选工作区/交接说明_history.md').write_bytes(b'')
