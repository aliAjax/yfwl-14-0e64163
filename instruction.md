# 山前台网复核

暴雨后，山前六台地震记录器的安装表与波形目录失去对应。台账仍保留六个候选接收点、八次爆破事件和每条现场记录的到时；各记录器时钟还带未知整体偏移。需要把台站重新归位，并恢复两层非均匀速度参数。

## 输入对象

`--input` 读取 UTF-8 JSON，示例报文位于 `/app/data/seismic_case.json`。

```text
顶层键：schema_version, case_id, sites, events, traces, bounds
接收点键：site_code, receiver_km
事件键：event_code, hypocenter_km
现场记录键：trace_code, arrival_s
边界键：min_sites, max_sites, min_events, max_events, max_abs_clock_shift_s, min_surface_km_s, max_surface_km_s, min_depth_gradient, max_depth_gradient
```

输入固定为六个接收点、八次事件和六条现场记录，三者编号各自唯一。每套坐标恰有东、北、深三个有限分量；每条 `arrival_s` 恰有八个有限值。

缺少键即拒绝。额外键即拒绝。同名键不得在同一层重复。格式版本只接受整数一，布尔值不属于版本类型。

## 正演与可识别边界

令接收点与事件的直线距离为 `distance_km`，深度和为 `depth_sum_km`，慢度为 `u = 1 / surface_km_s`。到时满足：

```text
arrival = event_origin_s + clock_shift_s + u * distance_km + depth_gradient * distance_km * depth_sum_km / 100
```

六个 `clock_shift_s` 的均值为零。`clock_shift_s` 的候选域为 `[-1.2, 1.2]` 秒。表层速度边界固定为 `[5.5, 7.5]` km/s，深度梯度边界固定为 `[-0.02, 0.02]`。

不要枚举速度网格或时钟偏移网格。对到时矩阵、距离项和深度项同时做行、列中心化后，`u` 与 `depth_gradient` 由 2×2 法方程闭式求出；时钟偏移由残差行均值恢复后整体减去均值。

## 联合反演

先用同一现场记录在八次事件间的中心化走时形态，与所有候选接收点的双参数走时形态计算代价，再用匈牙利算法求全局最小台站双射。恢复映射后，闭式求解速度和梯度，再以零均值规范化恢复六个时钟偏移。

速度参数在公开边界内唯一。时钟偏移统一采用零均值规范。

## 输出与检查点

成功时两个输出流均留空。

```text
输入错误：标准输出为空；标准错误一行 `seismic: `；状态二；无调用栈。
```

```text
结果外层：schema_version, case_id, station_map, velocity, fit, status
台站表：trace_code, site_code, clock_shift_s
速度项：surface_km_s, depth_gradient
拟合项：rms_s, max_abs_s
```

台站表按 `trace_code` 升序。`status` 为 `calibrated`。

检查点外层仅保存版本号与 `completed`。`completed` 每项键集合为 `case_id`、`fingerprint`、`station_map_sha256`。新输入追加一条；输入字节一致时检查点字节不变；失败输入保持旧字节。

报告与检查点均为紧凑单行 JSON，并以一个换行结束。两份文件先写同目录暂存件，再一次性换名。

验收时样例须正常退出，报告有六条映射，检查点写入一条。完整复核的公开耗时为六秒，测试看门狗八秒。
