import os
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

    print(f"[x] 已导出 JSON：{json_out}")
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
    
    
def embed_json_in_html(json_path, html_path):
    """Embed JSON data directly into the HTML file."""
    with open(json_path, "r", encoding="utf-8") as json_file:
        json_data = json_file.read()
    
    with open(html_path, "r", encoding="utf-8") as html_file:
        html_content = html_file.read()
    
    # Embed JSON data into a <script> tag in the HTML
    embedded_script = f"<script>const embeddedChatData = {json_data};</script>"
    if "</head>" in html_content:
        html_content = html_content.replace("</head>", f"{embedded_script}\n</head>")
    else:
        html_content = f"{embedded_script}\n{html_content}"
    
    with open(html_path, "w", encoding="utf-8") as html_file:
        html_file.write(html_content)