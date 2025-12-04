import os
import re
import csv
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory
import pdfplumber
from PIL import Image
import pytesseract
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 日志文件
WTP_LOG = "wtp_log.csv"
EVENT_LOG = "event_log.csv"

# 上传目录
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


def append_csv(path, row):
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")


@app.route("/health", methods=["GET"])
def health():
    return "ok"


@app.route("/api/decoded_bill", methods=["GET"])
def get_decoded_bill():
    # 固定样例，用于前端 demo
    data = {
        "provider": "NYC Imaging Center",
        "service_date": "2025-05-12",
        "procedure": "MRI Brain (CPT 70551)",
        "billed_amount": 1400,
        "allowed_amount": 900,
        "insurer_paid": 620,
        "printed_owe": 780,
        "should_owe": 180,
        "estimated_overcharge": 600,
        "issues": [
            "Potential duplicate facility fee for the same date of service.",
            "\"Out-of-network\" label appears inconsistent with typical directory data.",
            "Billed amount is roughly 2x the usual range for this MRI in this ZIP code."
        ]
    }
    return jsonify(data)


@app.route("/api/action_plan", methods=["GET"])
def get_action_plan():
    data = {
        "phone_script": (
            "Hi, I received a bill for an MRI on May 12, 2025. "
            "The bill says I owe $780, but based on my understanding of my plan, "
            "I believe I should owe about $180. "
            "Can you help me review the allowed amount and coinsurance "
            "for CPT 70551 at NYC Imaging Center?"
        ),
        "email_template": (
            "Subject: Request to review MRI bill for possible overcharge\n\n"
            "Hello,\n\n"
            "I am writing about a bill for an MRI on May 12, 2025 at NYC Imaging Center. "
            "The bill lists $780 as my responsibility, but based on my plan's typical "
            "coinsurance, I believe the correct amount should be closer to $180. "
            "Could you please review the allowed amount and my cost share for CPT 70551 "
            "and let me know if an adjustment is possible?\n\n"
            "Thank you."
        ),
        "checklist": [
            "Download your Explanation of Benefits (EOB) for this service.",
            "Request an itemized bill from the provider.",
            "Write down the date, time, and name of anyone you speak with.",
            "Save any emails or letters you send or receive."
        ]
    }
    return jsonify(data)


@app.route("/api/wtp", methods=["POST"])
def post_wtp():
    payload = request.get_json(force=True)
    choice = payload.get("choice")
    reason = payload.get("reason", "")
    user_id = payload.get("user_id", "")
    ts = datetime.utcnow().isoformat()

    row = {
        "timestamp": ts,
        "choice": choice,
        "reason": reason,
        "user_id": user_id
    }
    append_csv(WTP_LOG, row)
    return jsonify({"status": "ok"})


@app.route("/api/session_event", methods=["POST"])
def post_event():
    payload = request.get_json(force=True)
    event_type = payload.get("event_type", "")
    user_id = payload.get("user_id", "")
    extra = payload.get("extra", {})
    ts = datetime.utcnow().isoformat()

    row = {
        "timestamp": ts,
        "event_type": event_type,
        "user_id": user_id,
        "extra": str(extra)
    }
    append_csv(EVENT_LOG, row)
    return jsonify({"status": "ok"})


def extract_text_from_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()

    # PDF：先尝试 pdfplumber 提取文字，如果失败则用 OCR
    if ext in [".pdf"]:
        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_parts.append(t)

        combined_text = "\n".join(text_parts).strip()

        # 如果 pdfplumber 提取不到文字（扫描件），用 OCR
        if not combined_text:
            try:
                with pdfplumber.open(path) as pdf:
                    for page in pdf.pages:
                        # 将 PDF 页面转为图片再 OCR
                        img = page.to_image(resolution=300).original
                        t = pytesseract.image_to_string(img)
                        text_parts.append(t)
                combined_text = "\n".join(text_parts)
            except Exception as e:
                print(f"OCR fallback failed: {e}")

        return combined_text

    # 图片：用 pytesseract 做 OCR
    if ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
        img = Image.open(path)
        text = pytesseract.image_to_string(img)
        return text

    # 其他类型暂时返回空
    return ""


def parse_bill_text(text: str) -> dict:
    """
    规则解析 v3：
    - 先按行预处理和分类
    - 优先从“明细行”和“带关键字的 total 行”抓金额
    - 尝试区分 service date / statement date
    """

    lines_raw = text.splitlines()
    lines = [l.strip() for l in lines_raw if l.strip()]

    # -------- provider 猜测：header 前几行里像机构名的 --------
    provider = ""
    provider_candidates = []
    header_window = lines[:12]
    for line in header_window:
        if any(x in line for x in ["Patient", "Insurance", "Billing", "Statement", "Account", "Invoice", "Guarantor"]):
            continue
        if any(x in line for x in ["Center", "Clinic", "Hospital", "Medical", "Imaging", "Health", "Care"]):
            provider_candidates.append(line.strip())

    if provider_candidates:
        provider = min(provider_candidates, key=len)

    # -------- 日期解析：优先带 service/visit 的行，其次任意日期 --------
    date_pattern = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
    service_date = ""
    fallback_date = ""

    for line in lines:
        m = re.search(date_pattern, line)
        if not m:
            continue
        d = m.group(1)
        lower = line.lower()
        if any(k in lower for k in ["service date", "date of service", "dos", "visit date"]):
            service_date = d
            break
        if not fallback_date:
            fallback_date = d

    if not service_date:
        service_date = fallback_date

    # -------- procedure / CPT：优先 CPT xxx，其次 5 位代码出现在明细行 --------
    cpt_pattern = r"(CPT\s*\d{4,5})"
    cpt_match = re.search(cpt_pattern, text, re.IGNORECASE)
    procedure = ""
    if cpt_match:
        procedure = cpt_match.group(1)
    else:
        # 没有显式 CPT，就找一行里有 5 位数字 + 金额的行
        code_line = ""
        for line in lines:
            if re.search(r"\b\d{5}\b", line) and re.search(r"\d+\.\d{2}", line):
                code_line = line
                break
        if code_line:
            m = re.search(r"\b\d{5}\b", code_line)
            if m:
                procedure = m.group(0)

    if not procedure:
        procedure = "Unknown procedure"

    # -------- 金额匹配：允许逗号，统一成 float --------
    # 必须有小数点（避免匹配 PO BOX、账户号等纯整数）
    amount_pattern = r"\$?\s*([0-9]+(?:,[0-9]{3})*\.[0-9]{2})"

    def extract_amounts(line: str):
        ms = re.findall(amount_pattern, line)
        vals = [float(m.replace(",", "")) for m in ms]
        # 过滤掉不合理的金额，医疗账单通常不超过 100 万
        vals = [v for v in vals if 0 < v < 1000000]
        return vals

    all_amounts = []
    for line in lines:
        all_amounts.extend(extract_amounts(line))

    all_amounts_clean = all_amounts[:]

    billed_amount = 0.0
    allowed_amount = 0.0
    printed_owe = 0.0
    insurer_paid = 0.0

    # -------- 先找 total / amount due / allowed / plan paid 等聚合行 --------
    for line in lines:
        lower = line.lower()
        vals = extract_amounts(line)
        if not vals:
            continue

        # total charges / total amount / billed amount
        if any(k in lower for k in ["total charges", "total amount", "total billed", "billed amount", "total lab charges"]):
            cand = max(vals)
            if cand > billed_amount:
                billed_amount = cand

        # patient responsibility / amount due / you owe
        if any(k in lower for k in ["amount due", "you owe", "amount you owe", "patient responsibility"]):
            cand = max(vals)
            if cand > printed_owe:
                printed_owe = cand

        # allowed
        if any(k in lower for k in ["allowed amount", "plan allowed", "eligible amount"]):
            cand = max(vals)
            if cand > allowed_amount:
                allowed_amount = cand

        # insurance paid
        if any(k in lower for k in ["insurance paid", "plan paid", "benefit paid", "insurance payment"]):
            cand = max(vals)
            if cand > insurer_paid:
                insurer_paid = cand

    # -------- 再看“明细行”：行里同时有日期 + 代码 + 多个金额 --------
    detail_lines = []
    for line in lines:
        if re.search(date_pattern, line) and re.search(r"\b\d{4,5}\b", line) and len(extract_amounts(line)) >= 2:
            detail_lines.append(line)

    # 针对第一条明细行：通常格式类似
    # [date] [code] [desc...] [charge] [allowed] [plan paid] [you owe]
    if detail_lines:
        line = detail_lines[0]
        vals = extract_amounts(line)
        # 简单假设：最后一个是 you owe，倒数第二个是 plan paid，中间某个是 allowed，第一个最大的当 billed
        if vals:
            if billed_amount == 0.0:
                billed_candidate = max(vals)
                billed_amount = billed_candidate

            # 可能结构：[charge, allowed, plan_paid, you_owe]
            if len(vals) >= 4:
                if printed_owe == 0.0:
                    printed_owe = vals[-1]
                if insurer_paid == 0.0:
                    insurer_paid = vals[-2]
                if allowed_amount == 0.0:
                    allowed_amount = vals[-3]
            elif len(vals) == 3:
                # 结构可能是 [charge, plan_paid, you_owe]
                if printed_owe == 0.0:
                    printed_owe = vals[-1]
                if insurer_paid == 0.0:
                    insurer_paid = vals[-2]
            elif len(vals) == 2:
                # 最简单：[charge, you_owe]
                if printed_owe == 0.0:
                    printed_owe = vals[-1]

    # -------- 没抓到就回退到全局逻辑 --------
    if all_amounts_clean:
        if billed_amount == 0.0:
            billed_amount = max(all_amounts_clean)
        if printed_owe == 0.0:
            if len(all_amounts_clean) >= 2:
                printed_owe = sorted(all_amounts_clean, reverse=True)[1]
            else:
                printed_owe = billed_amount

    if allowed_amount == 0.0 and billed_amount > 0:
        allowed_amount = round(billed_amount * 0.65, 2)

    if insurer_paid == 0.0 and allowed_amount > 0:
        coinsurance_rate = 0.2
        insurer_paid = max(0.0, allowed_amount * (1 - coinsurance_rate))

    if printed_owe == 0.0:
        printed_owe = billed_amount

    # -------- 计算 should_owe 和 overcharge --------
    coinsurance_rate = 0.2
    should_owe = round(allowed_amount * coinsurance_rate, 2) if allowed_amount > 0 else 0.0
    estimated_overcharge = max(0.0, printed_owe - should_owe)

    issues = []
    if allowed_amount > 0 and billed_amount > allowed_amount * 1.5:
        issues.append("Billed amount appears significantly higher than a typical allowed amount.")
    if estimated_overcharge > 0:
        issues.append("Patient responsibility looks higher than expected for a typical coinsurance rate.")

    result = {
        "provider": provider or "Unknown provider",
        "service_date": service_date or "Unknown date",
        "procedure": procedure or "Unknown procedure",
        "billed_amount": round(billed_amount, 2),
        "allowed_amount": round(allowed_amount, 2),
        "insurer_paid": round(insurer_paid, 2),
        "printed_owe": round(printed_owe, 2),
        "should_owe": round(should_owe, 2),
        "estimated_overcharge": round(estimated_overcharge, 2),
        "issues": issues,
        "raw_text": text
    }
    return result

@app.route("/api/upload_bill", methods=["POST"])
def upload_bill():
    """
    接收 PDF 或图片，做 OCR/文本抽取 + 粗糙解析，返回 decoded 结果。
    """
    if "file" not in request.files:
        return jsonify({"error": "no file field"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "empty filename"}), 400

    filename = file.filename
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    text = extract_text_from_file(save_path)
    if not text.strip():
        return jsonify({"error": "could not extract text from file"}), 400

    decoded = parse_bill_text(text)
    return jsonify(decoded)


if __name__ == "__main__":
    app.run(debug=True)