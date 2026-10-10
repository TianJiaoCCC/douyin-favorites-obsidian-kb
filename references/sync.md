# 部署与同步

Python 3.10+，本地工具无需第三方库。路径以本地配置文件目录为基准。`config.local.json`、状态目录、输入 JSON 和知识库不能提交。

第一次使用：

```powershell
python scripts/kb.py --config config.local.json baseline --input favorites-snapshot.json
```

该文件必须包含所有当前收藏 ID，并声明 complete=true；只记录已有 ID，不调用 AI、不写笔记。日后发现新 ID才进入新增队列。基线不会删除已排队的新增资料；完整性是适配器/操作者的责任，本工具不能仅凭声明验证抖音列表确实完整。

宿主代理整理后手动导入：

```powershell
python scripts/kb.py --config config.local.json import --mode new --input prepared-notes.json
```

历史补录：把 mode 改为 backfill。用户暂停补录后不再调用此模式。日期过滤仍生效。

配置好自行安装的适配器后：

```powershell
python scripts/kb.py --config config.local.json sync
```

没有适配器或完整基线会明确退出，不会运行历史补录。sync 将 pending 项目留给下次重试；已删除成功笔记自动 suppression。账本与原始资料仅本地保存。

Windows 每日 20:00（可设置 Time 参数）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts/schedule.ps1 -ConfigPath "完整路径/config.local.json" -PythonExe "完整路径/python.exe" -Time "20:00"
```

任务按当前 Windows 用户、机器本地时间运行，需登录且未休眠；云端适配器需要网络和可用登录。创建计划并不等于首次同步成功。默认不重叠运行，不唤醒电脑、不改变全局代理设置。暂停任务使用任务计划程序的“禁用”；用户只暂停历史补录时不要禁用新增任务。

图谱：脚本生成每库“外源资料/分类/分类名.md”，各笔记回链所属分类，总主题导航链接各分类。这能产生分类节点；图谱实际布局由 Obsidian 决定。
