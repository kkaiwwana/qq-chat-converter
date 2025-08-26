# QQ-Chat-Converter

[English Documentation](README.md)

将 QQ 聊天记录导出的 *.mht 文件转换为结构化的 JSON 格式（包含图片文件夹）。同时提供一个简单的本地查看器。

## 准备聊天数据
QQ(< 9.7.25) -> 消息管理器 -> 选择联系人（群） -> "右键点击" -> 导出聊天记录为 *.mht 文件（这是唯一能保留图片的方式）。

## 快速开始
### Python 命令行方式
1. 通过 `git clone https://github.com/kkaiwwana/qq-chat-converter.git` 克隆仓库，然后 `cd qq-chat-converter`。

2. 使用命令 `python .\scripts\convert_mht.py [你的-MHT-文件路径]` 导出 *.mht 文件。默认输出目录是 `./out_dir`，文件夹名与你的 mht 文件相同。

3. 一个 `index.html` 文件会生成在 `out_dir/[MHT文件名]` 目录下，这是一个本地查看器。但是，你需要在本地 HTML 服务器上运行它，而不是直接双击打开（因为浏览器出于安全目的，通常会屏蔽这样的文件尝试Fetch本地的数据）。具体来说，使用 `python -m http.server 8000` 启动本地 HTML 服务器，然后访问 `http://localhost:8000/[你的输出目录路径]/index.html`。

### GUI 程序
只需运行 `python .\GUI\qq-chat-converter.py` 即可启动 GUI 程序并开始使用。

![gui_demo](assets/IMG/gui_demo.png)

> [!TIP]
> 你可以使用 `启动消息浏览器` 按钮来启动HTML服务器并浏览导出的聊天记录。此外，当未指定导出目录（或通过点击 `清空` 按钮清除）时，你也可以启动 HTML 服务器并访问任何你想打开的目录。**这样你就可以访问你的输出目录，浏览所有导出的聊天记录。**

### 可执行文件
你可以直接在发布页面下载可执行文件！开始使用吧 : )

> [!TIP]
> 通过 `pyinstaller -w --onefile --add-data "GUI\resources;resources" .\GUI\qq-chat-converter-gui.py` 自行构建。值得一提的是，如果你没有进行更进阶的打包配置，请尝试在一个最小的环境中打包，从而避免其包含大量无关的依赖包，增加文件尺寸以及减慢运行速度。

## 聊天记录浏览器
![browser_dmeo](assets/IMG/browser_demo.png)
使用这个简单且**本地**（离线可用；不包含任何网络请求，所以你的数据很安全！）的聊天记录浏览器（查看器）加载导出的 JSON 文件，你可以：

- 搜索消息并通过简单点击跳转到上下文。
- 清晰地查看结构化的转发消息和图片。
- 按日期筛选聊天记录，并且**只有包含聊天记录的日期才会显示在列表中**。可以自由地跳转到前一天/后一天。
- 通过简单点击查看完整分辨率的图片。

## 许可证
该项目依照GPL-3.0许可证开源哦. 如果对你有用的话，考虑给个Star吧！