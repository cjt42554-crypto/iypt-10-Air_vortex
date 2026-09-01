# HF3D P0-P review-binding material

状态：`NON_AUTHORIZING_REVIEW_BINDING_MATERIAL_COMPLETE`。

本材料只集中列出未来 Pro 父票据所需的机器绑定信息，不签发票据、不包含允许态 grant，也不授权任何动态样本。包含本材料的新提交及其 tree 身份被有意省略，必须在提交形成后由 Pro 从 Git 拓扑独立观测。

## 确定性身份

- JSON 字节 SHA-256：`d925abf8ee16723285165bd6d33c01d099d72f3925c839776ee1e4db65a791ff`
- Git object format：`sha1`
- 已复核 runtime 参考提交：`8a0c2fa564d9d6abfd84e5620379af11e5f858d3`
- 已复核 runtime 参考 tree：`57dc939af0bc923ef3076d1d14ea889573041cf7`
- REQUIRED_IMPLEMENTATION_FILES：`24` 项，全部记录与参考提交相同。
- formal source SHA-256：`dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8`，`58` 个文件，`1004894` 字节。

## Runtime implementation 精确记录

1. `90_候选工作区/current/17_HF3D性能优化/analyse_p0_performance_campaign.py`

   - mode：`100755`
   - Git blob：`bb72c8a7e273f59f8019186aa89a4c9197e435f3`
   - SHA-256：`110ea2abcd3ab733fc34cbb5e08123fb7f164afd6bab6d3011e9d91f1d357cb2`
   - bytes：`20110`

2. `90_候选工作区/current/17_HF3D性能优化/artifacts/p0/.gitignore`

   - mode：`100644`
   - Git blob：`fa585771b3d95fe5933f6d220654525d4edecb7d`
   - SHA-256：`a2f31eeed34abfe907b09fbc6813b8d84f92e3c9ee9570cd7279cdf596a9743c`
   - bytes：`55`

3. `90_候选工作区/current/17_HF3D性能优化/audit_p0_performance_harness.py`

   - mode：`100755`
   - Git blob：`c651da5ae46b92544642dedfdaa6dc06a5c75fd4`
   - SHA-256：`80044112fbd3d546c0f445791f01d9dabca8c0830bae38193a4c90cfdf0bfe04`
   - bytes：`20679`

4. `90_候选工作区/current/17_HF3D性能优化/libp0p_allocator_observer.so`

   - mode：`100755`
   - Git blob：`48663346b25a67d92e14c75f533eb5477c37e80a`
   - SHA-256：`ca06432f2f20388f9581b195fc78134704acc42095795ace7df6d6a3de158973`
   - bytes：`26496`

5. `90_候选工作区/current/17_HF3D性能优化/p0_performance_contract.py`

   - mode：`100644`
   - Git blob：`d248593ee85ccdaa5d639845d17adffb0cbaa9e3`
   - SHA-256：`d63ad140fa7f8236405e3d83f1543dd65d720c32a98e5471645a88b097102e27`
   - bytes：`26981`

6. `90_候选工作区/current/17_HF3D性能优化/p0p_allocation.py`

   - mode：`100644`
   - Git blob：`2861266f2dc17f756add2090dedf0047dad9694a`
   - SHA-256：`9132d3c9c43fc615133557d7015c4860f0fb05e1213039ac64d7651522d95072`
   - bytes：`17000`

7. `90_候选工作区/current/17_HF3D性能优化/p0p_allocator_observer.c`

   - mode：`100644`
   - Git blob：`2abec27a3345d846922784d99890a4816cb63890`
   - SHA-256：`b93b4539f3c7c4104a1e1c2c01533c71f9779699551a6c05b4d3b74f146eea13`
   - bytes：`38518`

8. `90_候选工作区/current/17_HF3D性能优化/p0p_allocator_probe`

   - mode：`100755`
   - Git blob：`bad6cb399076f8a0fdad9f51ee9f6027a8649b46`
   - SHA-256：`f1bb4e19500aee81115e6b14a3ebcf4be24917e0c3eb0d0c5278df5949c97948`
   - bytes：`17408`

9. `90_候选工作区/current/17_HF3D性能优化/p0p_allocator_probe.c`

   - mode：`100644`
   - Git blob：`e69158444460c5b0e34b4e51040f0d46fe752ee9`
   - SHA-256：`55a1752f7fbd65dd17d0d317ff9f270626bbf794966e0bd690aaec0f0babcc87`
   - bytes：`5788`

10. `90_候选工作区/current/17_HF3D性能优化/p0p_authorization.py`

   - mode：`100644`
   - Git blob：`6bf94d24160d98ec28f5f77b598db0cf098e95ed`
   - SHA-256：`9e5643ee1ecb973863c143d280e1fd3fe9f9f89fc4d09c3ed05bb79c127348e4`
   - bytes：`36477`

11. `90_候选工作区/current/17_HF3D性能优化/p0p_capability_probe.py`

   - mode：`100755`
   - Git blob：`a43fa31e6a7be405b038f267ffbab0c69dad57c5`
   - SHA-256：`2ec636e338362d8e45be00f3c3fee4b4d3a59077c0bdbcf419fdd1f7ef8c247d`
   - bytes：`5907`

12. `90_候选工作区/current/17_HF3D性能优化/p0p_consumption.py`

   - mode：`100644`
   - Git blob：`0fcf333e9bbe21373c76e7667dbe9c7bd2cc1855`
   - SHA-256：`a6772a1df91734dd981ee159a6fdb186a6019d2e39d5196ca5cb1ff62eebe940`
   - bytes：`32067`

13. `90_候选工作区/current/17_HF3D性能优化/p0p_cuda_mem_exact.py`

   - mode：`100755`
   - Git blob：`228353fce45230a6974f0b4ce7125027f1e236bc`
   - SHA-256：`8a3a99b6b284a56deb72a25b4ba19a30d98565cc54389f5393d253db7f358fee`
   - bytes：`1064`

14. `90_候选工作区/current/17_HF3D性能优化/p0p_execution_authorization.json`

   - mode：`100644`
   - Git blob：`81fc6e9dea20cf3c4eff2e4eb20fde3ac19a892b`
   - SHA-256：`ef0690d10551d857e6519a99808c2f4ca36b0e07d723cde1b23515594af825d6`
   - bytes：`893`

15. `90_候选工作区/current/17_HF3D性能优化/p0p_instrumentation.py`

   - mode：`100644`
   - Git blob：`435aa48da5a90e145409c19f6d38263eebef4c62`
   - SHA-256：`7a1fd72d30afdb9ee40a369b7ea4b4d1a11f1da37df9b92aaf6afca408932615`
   - bytes：`23809`

16. `90_候选工作区/current/17_HF3D性能优化/p0p_monitor.py`

   - mode：`100755`
   - Git blob：`986629cb84054dd0963e1e10fc562bb6b32dcd64`
   - SHA-256：`0d5e5cd2b46a48431db3eb48140ed10ff41fe1eaf76ebf0abe2c9d0213adce12`
   - bytes：`8614`

17. `90_候选工作区/current/17_HF3D性能优化/p0p_nsys.py`

   - mode：`100644`
   - Git blob：`8a241e31015600ed61303a34a4380ac6ebaf8ca8`
   - SHA-256：`4076d44712a2a23afd3543efbde36946d1598cf5945e983d65106bcd6a799c4d`
   - bytes：`23202`

18. `90_候选工作区/current/17_HF3D性能优化/p0p_numerical.py`

   - mode：`100644`
   - Git blob：`3de5173e162cefd5666de94dd9d3928c567db3f7`
   - SHA-256：`aa14425de909ff5c542aec1fd126f4b80f6ed96af78f512948844f9249ff5182`
   - bytes：`5584`

19. `90_候选工作区/current/17_HF3D性能优化/p0p_telemetry.py`

   - mode：`100644`
   - Git blob：`cba8e91e637e0a1a0aa3bffe309acbdc4f0fc4c9`
   - SHA-256：`59cb149b6855fbbe49f00c13c79ea9782a61b92b1c7d54111d300ae643e8f023`
   - bytes：`27073`

20. `90_候选工作区/current/17_HF3D性能优化/p0p_transaction.py`

   - mode：`100644`
   - Git blob：`e22255e75a6ce71030042eb71fa8090ad557048c`
   - SHA-256：`ab0cb6109442cdeba5e4bf84b46ac544ee43e735e0cc576e92c058bb40e7ff76`
   - bytes：`6952`

21. `90_候选工作区/current/17_HF3D性能优化/run_p0_performance_campaign.py`

   - mode：`100755`
   - Git blob：`4b139e7e02cc8791df47e4659888a2255a9ebd55`
   - SHA-256：`9966a132dcc83b2981ff53cba92dd58345593f39c210a6e534dc9d9eb4a993dc`
   - bytes：`33514`

22. `90_候选工作区/current/17_HF3D性能优化/run_p0_performance_sample.py`

   - mode：`100755`
   - Git blob：`02a0f1a6d5895d6fc2bb9799a3df52698fe2c5bb`
   - SHA-256：`14a060a140524d2c127a1544bac84b28ff2723366c91e0cc85a85465896ae7e7`
   - bytes：`53401`

23. `90_候选工作区/current/17_HF3D性能优化/tests/test_p0_performance_harness.py`

   - mode：`100644`
   - Git blob：`825616d22f524bb97d644029fd1f85625ff8ec04`
   - SHA-256：`14754fb3885a2ffe843baa750483cc4f390ecbf03f6ccc8f2c46253677aa568a`
   - bytes：`24926`

24. `90_候选工作区/current/17_HF3D性能优化/tests/test_p0p_authorization_and_consumption.py`

   - mode：`100644`
   - Git blob：`eafd041b52feecc8826858637bf125a01d70c4d8`
   - SHA-256：`72ffe00d09626c2039bf1d3996468f8c3f93dd7868c6c473a19f098bdae4159b`
   - bytes：`31371`

## 其他票据绑定记录

1. static audit：`90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_static_audit.json`

   - mode：`100644`
   - Git blob：`f62144f7e6b0b9c244a2b813404bebeec2cf0ba1`
   - SHA-256：`90c9774bd5f83427897fc7c50aacdd76a5ef5a46ac4e0dc2c0d7190d3f3677d6`
   - bytes：`104373`

2. P0 contract：`90_候选工作区/current/17_HF3D性能优化/00_P0性能与散度诊断预注册.json`

   - mode：`100644`
   - Git blob：`62f256574f67654d13f588e0505f63505d065907`
   - SHA-256：`4952d0d0cdedc7dadf992b9304ac6936867a5240ed17b5ac3d0048e510a56085`
   - bytes：`15156`

3. capability manifest：`90_候选工作区/current/17_HF3D性能优化/artifacts/p0/p0p_static/p0p_capability_manifest.json`

   - mode：`100644`
   - Git blob：`251aee4d30ea0367392f6644f181acb3adb83c18`
   - SHA-256：`1fa464373846b2358acf65309415afe5981a5df3f7ce1dec286455531905d04d`
   - bytes：`1490`

4. denied authorization：`90_候选工作区/current/17_HF3D性能优化/p0p_execution_authorization.json`

   - mode：`100644`
   - Git blob：`81fc6e9dea20cf3c4eff2e4eb20fde3ac19a892b`
   - SHA-256：`ef0690d10551d857e6519a99808c2f4ca36b0e07d723cde1b23515594af825d6`
   - bytes：`893`

5. P0-D evidence：`90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_access_ledger.json`

   - mode：`100644`
   - Git blob：`bb4a829f55ef26e0430f8de17acd2ed80682f23c`
   - SHA-256：`d6c380cc09e3eada938df9dfa9deab9106018511f935bbd196afe5d9e2ecbd0f`
   - bytes：`31077`

6. P0-D evidence：`90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.json`

   - mode：`100644`
   - Git blob：`d72c9e9d3590ede6c2913e93898a1bd4c4622343`
   - SHA-256：`d83f3bd42b819cdfadea51dd2153f183eee7f7d29fbde6111722fb6abc0cffa5`
   - bytes：`248609`

7. P0-D evidence：`90_候选工作区/current/17_HF3D性能优化/artifacts/p0/divergence_diagnostics.md`

   - mode：`100644`
   - Git blob：`a3a3922a706cfaf052b1bef0f115d7bd65c0e6ab`
   - SHA-256：`ec0be83f071ed5b60c6dd84024e3414583a7e03d38ef0b60d15f360267ccec42`
   - bytes：`2779`

## 固定 18 项 schedule

Schedule ID：`HF3D_P0P_FIXED_18_V1`。

Canonical SHA-256：`2138aea65041ff322a0cc2ce22945544d51d1e39db1929079f9f6265e494dceb`。

1. `WARMUP_B1`

   - benchmark：`B1`
   - mode：`WARMUP`
   - scored：`false`
   - source config SHA-256：`602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0`

2. `WARMUP_B2`

   - benchmark：`B2`
   - mode：`WARMUP`
   - scored：`false`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

3. `WARMUP_B3`

   - benchmark：`B3`
   - mode：`WARMUP`
   - scored：`false`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

4. `TIMED_R1_B1`

   - benchmark：`B1`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0`

5. `TIMED_R1_B2`

   - benchmark：`B2`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

6. `TIMED_R1_B3`

   - benchmark：`B3`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

7. `TIMED_R2_B2`

   - benchmark：`B2`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

8. `TIMED_R2_B3`

   - benchmark：`B3`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

9. `TIMED_R2_B1`

   - benchmark：`B1`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0`

10. `TIMED_R3_B3`

   - benchmark：`B3`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

11. `TIMED_R3_B1`

   - benchmark：`B1`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0`

12. `TIMED_R3_B2`

   - benchmark：`B2`
   - mode：`TIMED`
   - scored：`true`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

13. `PROFILED_B2_HOST`

   - benchmark：`B2`
   - mode：`PROFILE_HOST`
   - scored：`false`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

14. `PROFILED_B2_CUDA`

   - benchmark：`B2`
   - mode：`PROFILE_CUDA`
   - scored：`false`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

15. `PROFILED_B2_MEMORY`

   - benchmark：`B2`
   - mode：`PROFILE_MEMORY`
   - scored：`false`
   - source config SHA-256：`64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d`

16. `PROFILED_B3_HOST`

   - benchmark：`B3`
   - mode：`PROFILE_HOST`
   - scored：`false`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

17. `PROFILED_B3_CUDA`

   - benchmark：`B3`
   - mode：`PROFILE_CUDA`
   - scored：`false`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

18. `PROFILED_B3_MEMORY`

   - benchmark：`B3`
   - mode：`PROFILE_MEMORY`
   - scored：`false`
   - source config SHA-256：`c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37`

## 授权与执行状态

- material non-authorizing：`true`
- actual Pro ticket absent：`true`
- authorized grant absent：`true`
- dynamic samples authorized：`false`
- authorization status：`NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW`
- P1 authorized：`false`
- HF3D time steps、warm-up、scored、profile 与全部性能样本启动数均为 `0`。

## Future ticket policy preview

未来父票据必须从 JSON 中的 `ticket_schema_preview`、`review_parent_policy_preview` 和精确绑定记录生成。Pro 仍需在 supplement 提交形成后独立观察 snapshot I 的 commit/tree，再生成 exact grant bytes 和 authorized child blob。父提交只允许精确列明的 review 路径；授权文件、全部 immutable bound paths、正式理论、正式程序和 D3 均不得变化。

当前结论仍为 `P0-P dynamic samples = NOT_AUTHORIZED`。
