import os
import sys
import time
import threading
import customtkinter as ctk
from pathlib import Path
from tkinter import filedialog, messagebox
from http.server import HTTPServer, SimpleHTTPRequestHandler
import webbrowser



import re
import json
import email

from tqdm import tqdm  
from email import policy
from urllib.parse import unquote
from bs4 import BeautifulSoup


IMG_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/gif":  ".gif",
    "image/webp": ".webp",
}

DATETIME_RE = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[\s\u00A0\u202F]*\d{1,2}:\d{2}:\d{2}")
TIME_ONLY = re.compile(r"\b(\d{1,2}:\d{2}:\d{2})\b")
DATE_LINE_RE = re.compile(r"日期[:：]\s*(\d{4}-\d{2}-\d{2})")


def _safe_decode(b: bytes, charset_hint=None) -> str:
    for cs in [charset_hint, "utf-8", "gb18030", "gbk", "latin-1"]:
        if not cs:
            continue
        try:
            return b.decode(cs, errors="ignore")
        except Exception:
            pass
    return b.decode("latin-1", errors="ignore")


def _norm_keys(*vals):
    keys, seen = [], set()
    for v in vals:
        if not v:
            continue
        v = unquote(str(v)).strip()
        cands = [v]
        base = os.path.basename(v)
        cands.append(base)
        if v.lower().startswith("file:///"):
            cands.append(os.path.basename(v[8:]))
        if v.lower().startswith("cid:"):
            cid = v[4:]
            cands += [f"cid:{cid}", cid]
        root, _ = os.path.splitext(base)
        if root:
            cands.append(root)
        for c in cands:
            k = c.strip().lower()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
    return keys


def read_mht(mht_path):
    """
    读取 MHT 文件并解析 HTML 和附件内容。
    自动去除文件开头的无关头部信息。
    """
    with open(mht_path, "rb") as f:
        content = f.read()

    # 查找 "Content-Type" 行，确保从有用信息开始解析
    content_type_index = content.find(b"Content-Type:")
    if content_type_index != -1:
        content = content[content_type_index:]  # 从 "Content-Type" 行开始截取

    # 解析 MHT 文件内容
    msg = email.message_from_bytes(content, policy=policy.default)

    html_text = None
    attachments = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/html" and html_text is None:
            payload_bytes = part.get_payload(decode=True)
            charset = part.get_content_charset() or 'utf-8'
            if payload_bytes:
                html_text = _safe_decode(payload_bytes, charset)
            else:
                payload = part.get_payload(decode=False)
                if isinstance(payload, bytes):
                    html_text = _safe_decode(payload, charset)
                else:
                    html_text = str(payload)
        elif ctype.startswith("image/"):
            data = part.get_payload(decode=True)
            if not data:
                continue
            attachments.append({
                "name": part.get_filename() or part.get_param("name"),
                "content_id": (part.get("Content-ID") or "").strip("<>"),
                "content_location": part.get("Content-Location") or "",
                "content_type": ctype or "",
                "data": data,
            })
    if html_text is None:
        raise RuntimeError("未在 MHT 中找到 text/html 部分。")
    return html_text, attachments


def build_src_to_local_map(soup, attachments, html_out_path, image_dir_name="Image"):
    html_dir = os.path.dirname(os.path.abspath(html_out_path))
    image_dir = os.path.join(html_dir, image_dir_name)
    os.makedirs(image_dir, exist_ok=True)

    att_keys_list = []
    for att in attachments:
        keys = _norm_keys(att["name"], att["content_location"], att["content_id"])
        att_keys_list.append(keys)

    src_to_local = {}
    used_attachments = set()

    # 为 <img> 标签添加进度条
    img_tags = soup.find_all("img")
    for img in tqdm(img_tags, desc="Processing images", unit="img"):
        raw_src = (img.get("src") or "").strip()
        if not raw_src:
            continue
        src_keys = _norm_keys(raw_src)
        match_idx = None
        for idx, keys in enumerate(att_keys_list):
            if any(k in keys for k in src_keys):
                match_idx = idx
                break
        if match_idx is None:
            continue
        used_attachments.add(match_idx)
        att = attachments[match_idx]

        src_base = os.path.basename(unquote(raw_src))
        root, _ext = os.path.splitext(src_base)
        guessed_ext = IMG_EXT_BY_MIME.get(att["content_type"].lower(), "")
        out_name = (root or "image") + (guessed_ext or ".bin")
        out_path_abs = os.path.join(image_dir, out_name)
        i = 1
        while os.path.exists(out_path_abs):
            out_path_abs = os.path.join(image_dir, f"{root or 'image'}_{i}{guessed_ext or '.bin'}")
            i += 1
        with open(out_path_abs, "wb") as f:
            f.write(att["data"])
        out_rel = os.path.relpath(out_path_abs, html_dir)
        src_to_local[raw_src] = out_rel

    return src_to_local, used_attachments


def rewrite_html_img_srcs(soup, src_to_local, html_out_path, image_dir_name="Image"):
    html_dir = os.path.dirname(os.path.abspath(html_out_path))
    image_dir = os.path.join(html_dir, image_dir_name)
    os.makedirs(image_dir, exist_ok=True)
    for img in soup.find_all("img"):
        raw_src = (img.get("src") or "").strip()
        if not raw_src:
            continue
        if raw_src in src_to_local:
            img["src"] = src_to_local[raw_src]
        else:
            base = os.path.basename(unquote(raw_src))
            if not base:
                continue
            tentative_abs = os.path.join(image_dir, base)
            tentative_rel = os.path.relpath(tentative_abs, html_dir)
            img["src"] = tentative_rel


def _norm_space(s: str) -> str:
    """把各种奇怪空格统一成普通空格并折叠"""
    if s is None:
        return ""
    s = s.replace("\u00A0", " ").replace("\u202F", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_forwarded(container, src_to_local):
    """
    解析转发消息块（包含 <font> + <img> 混排），输出严格的 sender + time + text + images。
    container: 转发内容所在的 body <div> 节点
    """
    # 先按 <br> 断行，并把 <img> 作为独立行保留下来
    items = []  # [(kind, value)]  kind ∈ {"text","img"}
    buf = []    # 累积同一行的文本

    def flush_text():
        s = _norm_space("".join(buf))
        if s:
            items.append(("text", s))
        buf.clear()

    for node in container.descendants:
        name = getattr(node, "name", None)
        if name == "br":
            flush_text()
        elif name == "img":
            flush_text()
            raw_src = (node.get("src") or "").strip()
            if raw_src:
                items.append(("img", src_to_local.get(raw_src, raw_src)))
        elif name is None:  # 文本结点
            buf.append(str(node))
        # 其它标签（font/span/div等）不 flush，只等其子文本/子img自己触发
    flush_text()

    # 去掉空文本行
    items = [(k, v) for (k, v) in items if not (k == "text" and not v)]

    # 扫描“消息头”（支持 同行/分行 两种形式）
    headers = []
    n = len(items)
    i = 0
    while i < n:
        kind, val = items[i]
        if kind == "text":
            m = DATETIME_RE.search(val)
            if m:
                # 同一行：sender + datetime
                sender = _norm_space(val[:m.start()])
                time_  = _norm_space(m.group(0))
                trailing = _norm_space(val[m.end():])  # 时间后同一行的残余文本
                headers.append({
                    "sender": sender,
                    "time": time_,
                    "sender_idx": i,
                    "time_idx": i,
                    "trailing": trailing or None
                })
                i += 1
                continue
            # 分两行：本行是 sender，下一行是“纯时间”
            if i + 1 < n and items[i+1][0] == "text" and DATETIME_RE.fullmatch(items[i+1][1] or ""):
                sender = _norm_space(val)
                time_  = _norm_space(items[i+1][1])
                headers.append({
                    "sender": sender,
                    "time": time_,
                    "sender_idx": i,
                    "time_idx": i+1,
                    "trailing": None
                })
                i += 2
                continue
        i += 1

    # 未识别到头部：保底把整体作为一条无头消息
    if not headers:
        texts = [v for k, v in items if k == "text"]
        imgs  = [v for k, v in items if k == "img"]
        return [{
            "sender": None,
            "time": None,
            "text": "\n".join(texts) or None,
            "images": imgs or None,
            "image": imgs[0] if imgs else None
        }]

    # 按“当前头部的 time 行”到“下一条头部的 sender 行”为范围切正文，并归并图片
    forwarded = []
    for idx, h in enumerate(headers):
        start = h["time_idx"] + 1
        end   = headers[idx+1]["sender_idx"] if idx + 1 < len(headers) else n

        texts, imgs = [], []
        if h["trailing"]:
            texts.append(h["trailing"])

        j = start
        while j < end:
            k, v = items[j]
            if k == "text":
                texts.append(v)
            elif k == "img":
                imgs.append(v)
            j += 1

        forwarded.append({
            "sender": _norm_space(h["sender"]) or None,
            "time": h["time"],
            "text": ("\n".join(texts)).strip() if texts else None,
            "images": imgs or None,
            "image": imgs[0] if imgs else None
        })

    return forwarded


def parse_message(tr, src_to_local, current_date=None):
    td = tr.find("td")
    if not td:
        return None, current_date

    # 先判断是否是日期分隔行
    td_text = td.get_text(strip=True)
    m_date = DATE_LINE_RE.match(td_text)
    if m_date:
        return None, m_date.group(1)  # 更新当前日期，并不生成记录

    divs = [d for d in td.find_all("div", recursive=False)]
    if len(divs) < 2:
        return None, current_date

    # header
    header = divs[0]
    sender_div = header.find("div")
    sender = sender_div.get_text(strip=True) if sender_div else None
    header_text = header.get_text(" ", strip=True)
    m_time = TIME_ONLY.search(header_text)
    if not m_time:
        return None, current_date
    time = m_time.group(1)

    # body
    body = divs[1]
    text, forwarded = None, None
    fonts = body.find_all("font")
    if fonts and any(DATETIME_RE.search(f.get_text()) for f in fonts):
        forwarded = parse_forwarded(body, src_to_local)
        images = None
    else:
        parts = [f.get_text("\n", strip=True) for f in fonts] if fonts else []
        text = ("\n".join([p for p in parts if p]).strip() or None) if parts else None
        images = [src_to_local.get(img.get("src").strip(), img.get("src").strip())
                  for img in body.find_all("img") if img.get("src")]
        if not images:
            images = None

    return {
        "sender": sender,
        "date": current_date,
        "time": time,
        "text": text,
        "images": images,
        "image": images[0] if images else None,
        "forwarded": forwarded
    }, current_date


def export_from_mht(
    mht_path: str,
    json_out: str = "qq_chat.json",
    html_out: str = "qq_chat_extracted.html",
    image_dir_name: str = "Image",
):
    html_text, attachments = read_mht(mht_path)
    soup = BeautifulSoup(html_text, "lxml")
    
    # 构建 src_to_local 映射
    src_to_local, _ = build_src_to_local_map(soup, attachments, html_out, image_dir_name=image_dir_name)
    rewrite_html_img_srcs(soup, src_to_local, html_out, image_dir_name=image_dir_name)

    os.makedirs(os.path.dirname(os.path.abspath(html_out)) or ".", exist_ok=True)
    with open(html_out, "w", encoding="utf-8") as f:
        f.write(str(soup))

    records = []
    current_date = None
    rows = soup.find_all("tr")  # 获取所有 <tr> 元素

    # 使用 tqdm 显示进度条
    for tr in tqdm(rows, desc="Processing messages", unit="msg"):
        msg, current_date = parse_message(tr, src_to_local, current_date)
        if msg:
            records.append(msg)

    os.makedirs(os.path.dirname(os.path.abspath(json_out)) or ".", exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"\n[x] 已导出 JSON：{json_out}")
    print(f"[x] 已保存可直接打开的 HTML：{html_out}")
    print(f"[x] 已创建并写入图片目录：{os.path.join(os.path.dirname(os.path.abspath(html_out)), image_dir_name)}")
    

def deduplicate_images(image_dir, json_file, json_out=None):
    # 1) 扫描图片文件
    all_files = [f for f in os.listdir(image_dir) if os.path.isfile(os.path.join(image_dir, f))]
    
    # 2) 归组
    basename_map = {}  # 基础名 -> 第一个文件
    old_to_new = {}    # 旧文件名 -> 新保留文件名
    for f in all_files:
        # 去掉扩展名
        name, ext = os.path.splitext(f)
        # 去掉末尾的 _数字
        base_name = re.sub(r'_\d+$', '', name)
        if base_name not in basename_map:
            basename_map[base_name] = f
            old_to_new[f] = f
        else:
            # 重复文件，指向已有文件
            old_to_new[f] = basename_map[base_name]
            # 删除重复文件
            os.remove(os.path.join(image_dir, f))
    
    print(f"[x] 图片去重完成，共保留 {len(basename_map)} 张图片")

    # 3) 更新 JSON
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    def update_paths(msg):
        if not msg:
            return
        if msg.get("image"):
            msg["image"] = old_to_new.get(os.path.basename(msg["image"]), msg["image"])
        if msg.get("images"):
            msg["images"] = [old_to_new.get(os.path.basename(p), p) for p in msg["images"]]
        if msg.get("forwarded"):
            for fwd in msg["forwarded"]:
                update_paths(fwd)

    if isinstance(data, list):
        for msg in data:
            update_paths(msg)
    elif isinstance(data, dict) and "messages" in data:
        for msg in data["messages"]:
            update_paths(msg)
    else:
        raise RuntimeError("JSON 格式未知")

    # 4) 保存
    json_out = json_out or json_file
    with open(json_out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[x] JSON 更新完成：{json_out}")
        

class MHTConverterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Configure window
        self.title("QQ-Chat Converter")
        self.geometry("900x600")
        
        # Initialize variables
        self.mht_path = None
        self.output_dir = None
        self.server_thread = None
        self.port_running = None

        # Configure grid layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Create sidebar
        self.sidebar = ctk.CTkFrame(self, width=140, corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=4, sticky="nsew")
        self.sidebar.grid_rowconfigure(4, weight=1)
        
        # App logo/title
        self.logo_label = ctk.CTkLabel(self.sidebar, text="MHT\nConverter", 
                                      font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))
        
        # Main area
        # File selection frame
        self.file_frame = ctk.CTkFrame(self)
        self.file_frame.grid(row=0, column=1, padx=(20, 20), pady=(20, 0), sticky="nsew")
        
        # MHT file selection
        self.mht_label = ctk.CTkLabel(self.file_frame, text="MHT 文件:")
        self.mht_label.grid(row=0, column=0, padx=10, pady=10)
        
        self.mht_entry = ctk.CTkEntry(self.file_frame, width=400)
        self.mht_entry.grid(row=0, column=1, padx=10, pady=10)
        
        self.mht_button = ctk.CTkButton(self.file_frame, text="浏览", command=self.browse_mht)
        self.mht_button.grid(row=0, column=2, padx=10, pady=10)
        
        # Output directory selection
        self.output_label = ctk.CTkLabel(self.file_frame, text="输出目录:")
        self.output_label.grid(row=1, column=0, padx=10, pady=10)
        
        self.output_entry = ctk.CTkEntry(self.file_frame, width=400)
        self.output_entry.grid(row=1, column=1, padx=10, pady=10)
        
        self.output_button = ctk.CTkButton(self.file_frame, text="浏览", command=self.browse_output_dir)
        self.output_button.grid(row=1, column=2, padx=10, pady=10)
        
        # Add port input field (after output directory selection)
        self.port_label = ctk.CTkLabel(self.file_frame, text="本地服务端口:")
        self.port_label.grid(row=2, column=0, padx=10, pady=10)
        
        self.port_entry = ctk.CTkEntry(self.file_frame, width=100)
        self.port_entry.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        self.port_entry.insert(0, "8000")  # 设置默认端口值
        
        # Add validation for port input
        self.port_entry.bind('<KeyRelease>', self.validate_port)
        # Log area
        self.log_frame = ctk.CTkFrame(self)
        self.log_frame.grid(row=1, column=1, padx=(20, 20), pady=(20, 0), sticky="nsew")
        
        self.log_label = ctk.CTkLabel(self.log_frame, text="处理日志")
        self.log_label.pack(padx=10, pady=5)
        
        self.log_text = ctk.CTkTextbox(self.log_frame, width=600, height=300)
        self.log_text.pack(padx=10, pady=10, fill="both", expand=True)

        # Action buttons in sidebar
        self.start_button = ctk.CTkButton(self.sidebar, text="开始处理", 
                                         command=self.start_processing)
        self.start_button.grid(row=1, column=0, padx=20, pady=10)
        
        self.server_button = ctk.CTkButton(self.sidebar, text="启动消息浏览器", 
                                          command=self.start_html_server)
        self.server_button.grid(row=2, column=0, padx=20, pady=10)
        
        # Add clear button
        self.clear_button = ctk.CTkButton(self.sidebar, text="清空选择",
                                         command=self.clear_selections,
                                         fg_color="gray70",  # 使用灰色以区分其他按钮
                                         hover_color="gray60")
        self.clear_button.grid(row=3, column=0, padx=20, pady=10)

        # Redirect stdout and stderr
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        sys.stdout = self
        sys.stderr = self

    def browse_mht(self):
        """选择 MHT 文件"""
        file_path = filedialog.askopenfilename(filetypes=[("MHT Files", "*.mht")])
        if file_path:
            self.mht_path = file_path
            self.mht_entry.delete(0, ctk.END)
            self.mht_entry.insert(0, file_path)

    def browse_output_dir(self):
        """选择输出目录"""
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.output_dir = dir_path
            self.output_entry.delete(0, ctk.END)
            self.output_entry.insert(0, dir_path)

    def write(self, message):
        """优化后的写入方法，正确处理进度条和换行"""
        if not hasattr(self, '_buffer'):
            self._buffer = []
            self._last_update = 0
            self._last_line_incomplete = False
        
        # 检查是否是进度条输出
        if '\r' in message:
            self.log_text.configure(state="normal")
            try:
                # 如果上一行是不完整的，删除它
                if self._last_line_incomplete:
                    last_line_start = self.log_text.get("end-2c linestart", "end-1c")
                    if last_line_start.strip():
                        self.log_text.delete("end-2c linestart", "end-1c")
            except:
                pass
            
            # 写入新的进度信息
            self.log_text.insert("end", message.rstrip())
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
            self._last_line_incomplete = True
            self.update()
            return

        # 处理普通消息
        if message.endswith('\n'):
            self._buffer.append(message)
            self._last_line_incomplete = False
        else:
            self._buffer.append(message)
            self._last_line_incomplete = True
        
        # 立即刷新缓冲区的条件：
        # 1. 消息以换行符结束
        # 2. 距离上次更新超过100ms
        current_time = time.time()
        if message.endswith('\n') or current_time - self._last_update > 0.1:
            self.log_text.configure(state="normal")
            content = ''.join(self._buffer)
            if content.strip():
                self.log_text.insert("end", content)
                if not content.endswith('\n'):
                    self.log_text.insert("end", '\n')
                self.log_text.see("end")
            self.log_text.configure(state="disabled")
            self._buffer = []
            self._last_update = current_time
            self.update()

    def flush(self):
        """确保缓冲区中的所有内容都被写入"""
        if hasattr(self, '_buffer') and self._buffer:
            self.log_text.configure(state="normal")
            content = ''.join(self._buffer)
            if content.strip():
                self.log_text.insert("end", content)
                if not content.endswith('\n'):
                    self.log_text.insert("end", '\n')
                self.log_text.see("end")
            self.log_text.configure(state="disabled")
            self._buffer = []
            self._last_update = time.time()
            self.update()
    
    def get_resource_path(self, relative_path):
        """获取资源文件的路径，适配打包后的环境"""
        if getattr(sys, 'frozen', False):  # 如果是打包后的环境
            base_path = sys._MEIPASS  # PyInstaller 解压后的临时目录
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))  # 开发环境
        return os.path.join(base_path, relative_path)
    
    def start_processing(self):
        """开始处理 MHT 文件"""
        if not self.mht_path or not self.output_dir:
            messagebox.showerror("错误", "请先选择 MHT 文件和输出目录！")
            return

        # 禁用按钮，避免重复处理
        self.start_button.configure(state="disabled")
        self.server_button.configure(state="disabled")

        def process():
            try:
                print("开始处理 MHT 文件...")
                export_from_mht(
                    mht_path=self.mht_path,
                    json_out=os.path.join(self.output_dir, "qq_chat.json"),
                    html_out=os.path.join(self.output_dir, "qq_chat.html"),
                    image_dir_name="Image"
                )
                deduplicate_images(
                    image_dir=os.path.join(self.output_dir, "Image"),
                    json_file=os.path.join(self.output_dir, "qq_chat.json"),
                    json_out=os.path.join(self.output_dir, "qq_chat.json")
                )

                # 从资源中提取 index.html
                index_html_src = self.get_resource_path("resources/index.html")
                index_html_dst = os.path.join(self.output_dir, "index.html")
                with open(index_html_src, "r", encoding="utf-8") as src, open(index_html_dst, "w", encoding="utf-8") as dst:
                    dst.write(src.read())

                print("处理完成！")
            except Exception as e:
                print(f"处理失败: {e}")
            finally:
                # 重新启用按钮
                self.after(0, lambda: self.start_button.configure(state="normal"))
                self.after(0, lambda: self.server_button.configure(state="normal"))

        threading.Thread(target=process).start()
        
    def validate_port(self, event=None):
        """验证端口输入是否有效"""
        try:
            port = int(self.port_entry.get())
            if 1024 <= port <= 65535:
                self.port_entry.configure(text_color=("black", "white"))  # 正常颜色
                return True
            else:
                self.port_entry.configure(text_color="red")  # 错误提示
                return False
        except ValueError:
            self.port_entry.configure(text_color="red")  # 错误提示
            return False

    def get_port(self):
        """获取当前端口号，如果无效则返回默认值 8000"""
        try:
            port = int(self.port_entry.get())
            if 1024 <= port <= 65535:
                return port
        except ValueError:
            pass
        return 8000
    
    def start_html_server(self):
        """启动 HTML Server 并打开浏览器"""
        temp_dir_flg = False
        if not self.output_dir:
            # 如果输出目录为空，提示用户选择目录
            dir_path = filedialog.askdirectory(title="选择 HTML Server 的根目录")
            if not dir_path:
                messagebox.showerror("错误", "未选择任何目录！")
                return
            self.output_dir = dir_path
            temp_dir_flg = True

        # 获取端口号
        port = self.get_port()

        def run_server():
            # 获取用户 home 路径
            home_dir = os.path.expanduser("~")
            # 创建 handler，指定根目录为 home
            handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=home_dir, **kwargs)

            try:
                server = HTTPServer(("localhost", port), handler)
                print(f"HTML Server 已启动: http://localhost:{port}/ -> {home_dir}")
                server.serve_forever()
            except OSError as e:
                print(f"启动服务器失败: 端口 {port} 可能已被占用")
                # 在主线程中显示错误消息
                self.after(0, lambda: messagebox.showerror("错误", f"启动服务器失败: 端口 {port} 可能已被占用"))

        if port != self.port_running or (self.server_thread is None or not self.server_thread.is_alive()):
            self.server_thread = threading.Thread(target=run_server, daemon=True)
            self.server_thread.start()
            self.port_running = port

        # 使用完整路径构造 URL
        rel_path = os.path.relpath(self.output_dir, str(Path.home()))
        rel_path = f"{rel_path.replace(os.sep, '/')}"

        url = f"http://localhost:{port}/{rel_path.replace(os.sep, '/')}"
        print(f"打开浏览器访问: {url}")
        webbrowser.open(url)
        
        if temp_dir_flg:
            self.output_dir = None

    def clear_selections(self):
        """清空所有已选择的文件和目录"""
        # 清空变量
        self.mht_path = None
        self.output_dir = None
        
        # 清空输入框
        self.mht_entry.delete(0, ctk.END)
        self.output_entry.delete(0, ctk.END)
        
        # 重置端口为默认值
        self.port_entry.delete(0, ctk.END)
        self.port_entry.insert(0, "8000")
        
        # 清空日志区域
        self.log_text.configure(state="normal")
        self.log_text.delete(1.0, ctk.END)
        self.log_text.configure(state="disabled")
        
        # 重置缓冲区
        if hasattr(self, '_buffer'):
            self._buffer = []
            self._last_update = 0
            self._last_line_incomplete = False
        
        print("已清空所有选择。")

    def __del__(self):
        """恢复标准输出"""
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr


if __name__ == "__main__":
    app = MHTConverterApp()
    app.mainloop()