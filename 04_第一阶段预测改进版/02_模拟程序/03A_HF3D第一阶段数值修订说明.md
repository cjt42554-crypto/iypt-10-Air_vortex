# HF3D 第一阶段数值修订

本文件描述 `airvortex.stage1` 隔离覆盖层；原 HF3D 源码保持不动。

## 1. 面通量 CFL

采用逐单元 donor-cell 流出系数

$$
\Lambda_{ijk}=
\frac{\max(u_R,0)+\max(-u_L,0)}{\Delta x}
+\frac{\max(v_T,0)+\max(-v_B,0)}{\Delta y}
+\frac{\max(w_U,0)+\max(-w_D,0)}{\Delta z},
$$

并令 $\Delta t_{adv}=C_{CFL}/\max\Lambda$。相反方向的两面速度不允许在中心平均中抵消。

## 2. 相—密度—动量一致输运

液相面通量为 $F_\alpha$，体积通量为 $F_V$，混合物质量通量统一为

$$
F_\rho=\rho_gF_V+(\rho_\ell-\rho_g)F_\alpha.
$$

三分量动量使用同一 $F_\rho$ 和同一 donor-cell 供体方向输运。界面有界性修复限制在扩展界面带内，并保持局部输运后速度；若局部容量不足则失败关闭，不再把质量误差全域平均摊销。

## 3. 三维气道瓶颈

取消“一个水平截面所有顶部连通气体总面积换算等效半径”的颈宽定义，改为顶部大气到磁子目标区的三维最宽路径瓶颈：

$$
r_{\rm throat}=\max_{\mathcal P}\min_{i\in\mathcal P}d_i.
$$

## 4. 证据边界

本阶段没有因为代码重排就把 HF3D 宣称为物理预测 GO。静滴 Laplace 压差、寄生流网格收敛、毛细波频率/阻尼、动态网格与界面联合收敛、真实磁子轨迹和独立实验验证仍是后续硬门。
