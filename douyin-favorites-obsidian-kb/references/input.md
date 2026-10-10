# 输入 JSON 契约

格式为 `{ "items": [...], "complete": true }`。`complete` 对完整收藏基线是必需条件；少量试扫不能当完整基线。输入只含公开来源内容，不含认证信息。

每个项目：

| 字段 | 意义 |
| --- | --- |
| id | 来源中的稳定 ID；不能用标题去重 |
| source | douyin / tiktok / book / personal |
| kind | video / image_post / book / text |
| published_at | 带时区的 ISO 日期；抖音与 TikTok 按发布日期筛选 |
| vault | 可选，配置中的库名 |
| title, author, url | 来源标题、作者和原链接（书籍可为空链接） |
| summary, body | 已经整理的中文摘要与 Markdown 正文；不是原始稿冒充总结 |
| category, subcategory, tags | 中文主题、子类、标签 |
| raw_text | 原稿或图片 OCR；书籍只保存所需摘录及页码，避免重复整本 |
| assets | 可选，本地图片的路径列表，相对输入文件目录解析；禁止目录穿越 |

`baseline` 只需要 id/source/kind/published_at/vault。`import` 还需要 title/summary/body/category；body 至少 40 字。长正文不会截断，不用字数比例填充。

外部适配器接收最后两个参数 `--request <request.json>`，将响应文件写入请求里的 `output_path`。请求 mode 为 new、包含 baseline ID、completed ID、suppressed ID、publication_since、process_limit。适配器应先比较 ID，只为待处理项目下载及调用 AI；不得先总结所有收藏再让本工具跳过。

适配器必须验证其配置来源只包含用户收藏，刷新过期链接，管理登录/下载/ASR/OCR及 AI 调用。此仓库不提供抖音采集适配器，也不分发第三方采集器；用户可在获得相应许可后自行安装。已有受限上游代码不要提交到这个仓库。
