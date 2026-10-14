# 2026-10-14 依赖审计

本次维护同步对仓库原锁文件执行了 `npm audit --package-lock-only`，原结果为 17 项告警：3 critical、12 high、2 moderate。

将 oxfmt 从 0.61.0 更新为 0.67.0，并通过 `npm audit fix --package-lock-only --ignore-scripts` 更新允许版本范围内的安全补丁后，结果为 9 high、0 critical、0 moderate。

剩余报告涉及 braces / micromatch / fast-glob，以及使用这些依赖的 vinext、vite-plugin-commonjs、vite-plugin-dynamic-import、shadcn 和 ts-morph 依赖链。这些报告包含上游尚未发布可直接兼容替代的修复，或需要更新上层依赖的情况。审计工具建议的 vinext 0.0.15 与 shadcn 1.0.0 是旧主版本替换，不适合作为本次维护同步的自动修复。

- 保留 `npm audit --audit-level=high` 的 CI 检查。因此其他功能检查通过时，安全审计步骤仍可能失败，不能将整套 CI 标记为已全部通过。
- 不使用 `npm audit fix --force` 自动降级框架，也不以关闭审计、忽略告警或删掉依赖来声称问题已解决。
- 本次依赖更新仅提交到社区源码与锁文件，没有修改作者站点的生产镜像。后续升级框架或组件工具链时，应独立验证构建、登录与部署兼容性。

审计结果随上游更新变化，复核命令：

```bash
npm ci
npm audit --package-lock-only
```
