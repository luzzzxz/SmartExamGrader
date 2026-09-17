# -*- coding: utf-8 -*-
"""
多模态大模型（Vision LLM）智能识图、涂改过滤与主观题自动批改服务模块。
支持 OpenAI 兼容格式接口（如阿里云百炼通义千问 Qwen-VL-Plus、Qwen-VL-Max、本地 Ollama 等）。
纯标准库实现，零第三方网络库依赖。
"""

import os
import re
import json
import math
import time
import io
import base64
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PIL import Image, ImageEnhance

try:
    from core.persistence import atomic_write_json
except ImportError:
    try:
        from app.core.persistence import atomic_write_json
    except ImportError:
        def atomic_write_json(path, data):
            p = Path(path)
            tmp = p.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(p)


DEFAULT_CONFIG = {
    "enabled": True,
    "endpoint": "https://ws-rl118em2hjsr5a7t.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "api_key": "",
    "model": "qwen-vl-max",
    "concurrency": 3,
    "timeout": 30,
    "confidence_threshold": 0.85,
    "strictness": "strict",  # "strict" (极严格/中高考级) | "standard" (标准学术) | "lenient" (宽容理解)
    "custom_rules": (
        "1. 规范术语要求：学科专有名词严禁使用生活俗语代替（如‘反光’不可替代‘反射’，‘融化’不可替代‘熔化’）；\n"
        "2. 变化趋势题规则：若为选词填空，必须严格从题目给定选项中选词；若为自由填空，同向变化趋势词（如‘增大’与‘变大’、‘升高’与‘变高’）算对给满分；\n"
        "3. 科学逻辑铁律：物理因果不可倒置（‘反射角等于入射角’正确，‘入射角等于反射角’判0分；‘电流与电压成正比’正确，‘电压与电流成正比’判0分）；长句因果正确语义等价判满分；\n"
        "4. 严禁脑补推测考生心理，只认卷面实际墨迹作答（如学生写‘S点’绝不脑补‘S点下方’，写‘点下方’绝不脑补‘S’）；\n"
        "5. 出现两个矛盾答案的一律判 0 分。"
    ),
}


def check_physics_causality(student_text: str, standard_text: str) -> tuple[bool, str]:
    """
    检查物理学科规律中自变量与因变量是否存在因果颠倒。
    物理规律中自变量（因/条件）在前、因变量（果/结论）在后。
    返回 (is_inverted, reason)
    """
    clean_stu = re.sub(r'[\s\u3000\.,!?;:，。！？；：、_—\(\)（）\-\+]+', '', str(student_text or ''))
    clean_std = re.sub(r'[\s\u3000\.,!?;:，。！？；：、_—\(\)（）\-\+]+', '', str(standard_text or ''))

    # 1. 光的反射定律：反射角等于入射角（反射角随入射角改变）
    if '反射角' in clean_std and '入射角' in clean_std and any(w in clean_std for w in ('等于', '相等', '大小')):
        pos_ru = clean_stu.find('入射角')
        pos_fan = clean_stu.find('反射角')
        if pos_ru != -1 and pos_fan != -1:
            if pos_ru < pos_fan:
                return True, "因果关系颠倒：入射角是自变量/条件，反射角是因变量/结果，必须表述为‘反射角等于入射角’，不可颠倒为‘入射角等于反射角’"

    # 2. 欧姆定律：电流与电压成正比（电流随电压改变）
    if '电流' in clean_std and '电压' in clean_std and any(w in clean_std for w in ('正比', '反比')):
        pos_dianya = clean_stu.find('电压')
        pos_dianliu = clean_stu.find('电流')
        if pos_dianya != -1 and pos_dianliu != -1:
            if pos_dianya < pos_dianliu:
                return True, "因果关系颠倒：电压是自变量/原因，电流是因变量/结果，必须表述为‘电流与电压成正比’，不可颠倒为‘电压与电流成正比’"

    # 3. 浮力定律：浮力与排开液体的体积/重力成正比
    if '浮力' in clean_std and '排开' in clean_std and any(w in clean_std for w in ('正比', '反比', '等于', '相等')):
        pos_paikai = clean_stu.find('排开')
        pos_fuli = clean_stu.find('浮力')
        if pos_paikai != -1 and pos_fuli != -1 and pos_paikai < pos_fuli:
            return True, "因果关系颠倒：排开液体的体积/重力是自变量，浮力是因变量"

    return False, ""


def match_trend_direction(student_text: str, standard_text: str) -> tuple[bool, bool, str]:
    """
    检查在自由填空中，学生作答与标准答案是否表达了相同的物理量变化趋势。
    返回 (is_trend_question, is_same_trend, direction_name)
    """
    clean_stu = re.sub(r'[^\u4e00-\u9fa5]', '', str(student_text or ''))
    clean_std = re.sub(r'[^\u4e00-\u9fa5]', '', str(standard_text or ''))

    increase_words = {'增大', '变大', '增加', '增多', '升高', '变高', '变快', '加快', '变强', '增强', '变粗', '变长', '加速', '变深', '变亮', '变重'}
    decrease_words = {'减小', '变小', '减少', '变少', '降低', '变低', '变慢', '减慢', '变弱', '减弱', '变细', '变短', '减速', '变浅', '变暗', '变轻'}
    constant_words = {'不变', '保持不变', '恒定', '恒定不变', '没有变化', '大小不变', '不改变'}

    def get_dir(s):
        if s in increase_words: return 'increase'
        if s in decrease_words: return 'decrease'
        if s in constant_words: return 'constant'
        return None

    std_dir = get_dir(clean_std)
    if not std_dir:
        return False, False, ""
    stu_dir = get_dir(clean_stu)
    if not stu_dir:
        return False, False, std_dir
    return True, (std_dir == stu_dir), std_dir


def get_config_file_path() -> Path:
    """获取配置文件路径"""
    app_dir = Path(__file__).resolve().parent
    return app_dir / "llm_grading_config.json"


def load_llm_config() -> dict:
    """载入大模型配置，若不存在则创建默认配置"""
    cfg_path = get_config_file_path()
    merged = dict(DEFAULT_CONFIG)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('大模型配置必须为 JSON 对象')
            merged.update(data)
        except (OSError, ValueError):
            merged['enabled'] = False
    else:
        save_llm_config(merged)
    if os.environ.get('ANSWER_CARD_LLM_API_KEY'):
        merged['api_key'] = os.environ['ANSWER_CARD_LLM_API_KEY']
    return merged


def save_llm_config(config: dict) -> bool:
    """保存配置到文件"""
    cfg_path = get_config_file_path()
    try:
        atomic_write_json(cfg_path, config)
        return True
    except Exception:
        return False


def test_llm_connection(config: dict = None) -> tuple[bool, str]:
    """
    测试大模型接口连通性
    :return: (is_success, message)
    """
    cfg = config or load_llm_config()
    endpoint = cfg.get("endpoint", "").strip().rstrip("/")
    api_key = cfg.get("api_key", "").strip()
    model = cfg.get("model", "qwen-vl-plus").strip()

    if not endpoint:
        return False, "接口地址 (Base URL) 不能为空。"
    if not api_key:
        return False, "API Key 不能为空。"

    url = f"{endpoint}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请回复【连接正常】四个字。"}
                ],
            }
        ],
        "max_tokens": 50,
    }

    start_time = time.time()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            res = json.loads(raw)
            cost_ms = int((time.time() - start_time) * 1000)
            msg = res.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return True, f"连接成功！耗时 {cost_ms}ms\n模型回复：{msg[:100]}"
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        return False, f"HTTP 错误 {e.code}：{e.reason}\n{err_body[:200]}"
    except urllib.error.URLError as e:
        return False, f"网络连接失败：{e.reason}"
    except Exception as e:
        return False, f"接口请求异常：{e}"


def prepare_llm_crop_image_bytes(image_path: str) -> bytes:
    """
    对主观题手写切片进行自适应高清化与补白预处理：
    1. 针对填空横线切片过扁过矮（高度通常仅 30~60px）导致大模型 Vision Transformer 分块模糊失真的问题，
       自适应等比放大（保持宽高比），使高度达到 160~220px 之间；
    2. 四周补白 24px 纯白边距（Padding），防止边缘撇捺笔画紧贴图片边界被 ViT 截断；
    3. 轻微拉伸对比度，增强黑色墨迹与纸面反差；
    4. 内存中输出高质量 JPEG 字节流，零磁盘垃圾。
    """
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"切片图片不存在：{image_path}")

    with Image.open(p) as img:
        img = img.convert("RGB")
        w, h = img.size

        # 1. 目标高度：至少 160px，但不超过 400px
        if h < 160:
            scale = max(2.0, 180.0 / max(1, h))
            new_w = int(round(w * scale))
            new_h = int(round(h * scale))
            if new_w > 1200:
                scale = 1200.0 / max(1, w)
                new_w = 1200
                new_h = max(160, int(round(h * scale)))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 2. 轻微对比度增强 (1.15 倍)，让细微手写笔迹更突出
        try:
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.15)
        except Exception:
            pass

        # 3. 四周添加 24px 纯白 Padding，避免边缘字迹贴边
        pad_x, pad_y = 24, 24
        padded_w = img.width + pad_x * 2
        padded_h = img.height + pad_y * 2
        padded_img = Image.new("RGB", (padded_w, padded_h), "white")
        padded_img.paste(img, (pad_x, pad_y))

        # 4. 导出为内存 JPEG
        buf = io.BytesIO()
        padded_img.save(buf, format="JPEG", quality=95)
        return buf.getvalue()


def _image_file_to_base64_url(image_path: str) -> str:
    """读取本地切片图片并经高清自适应预处理后转为 base64 data URI"""
    try:
        raw_bytes = prepare_llm_crop_image_bytes(image_path)
        encoded = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"切片图片不存在：{image_path}")
        with open(p, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"


def _extract_json_from_llm_response(raw_content: str) -> dict:
    """从大模型回复的文本中鲁棒提取 JSON 对象"""
    if not raw_content:
        return {}

    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_content, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except Exception:
            pass

    brace_match = re.search(r"(\{.*\})", raw_content, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except Exception:
            pass

    try:
        return json.loads(raw_content)
    except Exception:
        pass

    return {}


def grade_single_subjective_task(
    task_item: dict,
    expected_answers: list[str],
    expected_keywords: list[str],
    config: dict = None,
    ocr_config: dict = None,
) -> dict:
    """
    调用多模态大模型批改单个主观题切片任务。
    :param task_item: 任务字典，包含 crop_path, part, entry 等
    :param expected_answers: 标准备选答案列表
    :param expected_keywords: 核心采分关键词列表
    :param config: 大模型配置字典
    :param ocr_config: 该空位的完整OCR与题型配置字典（包含 unique_answer, choice_fill, prefix, suffix 等）
    :return: {
        'success': bool,
        'recognized_text': str,
        'confidence': float,
        'score': float,
        'max_score': float,
        'has_correction': bool,
        'needs_manual': bool,
        'reason': str,
        'error': str or None,
        'model': str
    }
    """
    cfg = config or load_llm_config()
    endpoint = cfg.get("endpoint", "").strip().rstrip("/")
    api_key = cfg.get("api_key", "").strip()
    model = cfg.get("model", "qwen-vl-plus").strip()
    timeout = int(cfg.get("timeout", 30))
    conf_threshold = float(cfg.get("confidence_threshold", 0.85))

    part = task_item.get("part", {})
    max_score = float(part.get("score") if part.get("score") is not None else 1.0)
    if not math.isfinite(max_score) or max_score < 0:
        raise ValueError("题目满分必须是非负有限数值")
    label = part.get("label", "主观题")
    crop_path = task_item.get("crop_path", "")

    # 提取题型与答案方案配置
    ocr_cfg = ocr_config or task_item.get("ocr_config") or (part.get("ocr") if isinstance(part, dict) else None) or {}
    prefix = str(ocr_cfg.get("prefix") or "").strip()
    suffix = str(ocr_cfg.get("suffix") or "").strip()
    candidate_words = str(ocr_cfg.get("candidate_words") or "").strip()
    unique_answer = bool(ocr_cfg.get("unique_answer", False))
    choice_fill = bool(ocr_cfg.get("choice_fill", False)) or bool(candidate_words)

    if not crop_path or not os.path.exists(crop_path):
        return {
            "success": False,
            "recognized_text": "",
            "confidence": 0.0,
            "score": 0.0,
            "max_score": max_score,
            "has_correction": False,
            "needs_manual": True,
            "reason": "切片图片未找到",
            "error": "切片图片路径不存在",
            "model": model,
        }

    try:
        img_data_url = _image_file_to_base64_url(crop_path)
    except Exception as e:
        return {
            "success": False,
            "recognized_text": "",
            "confidence": 0.0,
            "score": 0.0,
            "max_score": max_score,
            "has_correction": False,
            "needs_manual": True,
            "reason": f"图片读取失败: {e}",
            "error": str(e),
            "model": model,
        }

    answers_str = " / ".join(expected_answers) if expected_answers else "未明确提供，请按学科常规标准评判"
    keywords_str = " / ".join(expected_keywords) if expected_keywords else "无特定关键词要求"

    strictness = str(cfg.get("strictness", "strict")).strip().lower()
    custom_rules = str(cfg.get("custom_rules", "")).strip()

    # 1. 诱导字与上下文提示块
    induction_block = ""
    if prefix or suffix:
        pref_line = f"- 横线左侧紧邻文字：‘{prefix}’\n" if prefix else ""
        suff_line = f"- 横线右侧紧邻文字：‘{suffix}’\n" if suffix else ""
        induction_block = (
            "【横线前后语境提示（辅助辨析学生笔迹）】：\n"
            f"{pref_line}"
            f"{suff_line}"
            "请结合横线前后语境辨析学生笔迹（如前后有‘透镜’时辅助区分‘凸’与‘凹’），但【只输出学生在横线上手写的字】，严禁把前导词或后置词重复计入识别文字中！\n\n"
        )

    # 2. 物理学科因果与逻辑铁律提示块
    physics_block = (
        "【物理学科因果与科学逻辑铁律（因果绝对不可颠倒，同义长句允许给满分）】：\n"
        "1. 【严禁因果颠倒】：物理规律具有自变量（因/条件）在前、因变量（果/结论）在后的严格单向性！\n"
        "   - 光的反射定律：必须是‘反射角等于入射角’。若学生写成‘入射角等于反射角’属于颠倒因果，【坚决判 0.0 分】！\n"
        "   - 欧姆定律：必须是‘电流与电压成正比’。若学生写成‘电压与电流成正比’属于颠倒因果，【坚决判 0.0 分】！\n"
        "   - 浮力与排开液体：必须是‘浮力与排开液体的体积/重力成正比’。若颠倒自变量与因变量，【坚决判 0.0 分】！\n"
        "2. 【逻辑正确且语义等价的长句允许给满分】：\n"
        f"   - 只要因果逻辑正确（如反射角在先作为结果，入射角在后作为基准），句式变动或修饰（例如：‘反射角的度数和入射角的度数相等’、‘导体中的电流跟导体两端的电压成正比’），【必须判定为正确，判满分 {max_score} 分】！\n\n"
    )

    # 3. 题型针对性要求提示块
    if choice_fill:
        cands_disp = candidate_words if candidate_words else answers_str
        type_block = (
            f"【题型限制：本题为选词填空题（选项限定：{cands_disp}）】：\n"
            "   - 学生必须严格从题目给定选项中选词作答，【严禁任何语义放宽或生活同义词替换】！\n"
            "   - 例如选项包含‘增大’，若学生写‘变大’，因未按要求选词，一律判 0.0 分！\n\n"
        )
    elif unique_answer:
        type_block = (
            "【题型限制：本题为物理专有名词/唯一答案客观题】：\n"
            "   - 必须使用规范学科专有名词且与参考答案精准字面匹配，严禁近义词放水，错字漏字一律判 0.0 分！\n\n"
        )
    else:
        is_trend_std, _, _ = match_trend_direction("", answers_str)
        if is_trend_std:
            type_block = (
                "【题型提示：本题为自由填空物理量变化趋势题（非选词填空）】：\n"
                f"   - 若学生作答与参考答案物理量变化方向相同（例如参考答案为‘增大’，学生写‘变大’、‘增加’；参考答案为‘减小’，学生写‘变小’；参考答案为‘变快’，学生写‘加快’），属于正确同向表述，【应判定为正确，判满分 {max_score} 分】！\n\n"
            )
        else:
            type_block = ""

    if strictness == "strict":
        strict_block = (
            "4. 【严格阅卷规范（按答案方案要求精准执行）】：\n"
            "   - 必须遵循学科专业术语规范，且符合参考答案或核心采分要点；\n"
            "   - 【严禁充当和事佬与善意脑补】：绝不能同情给分！若答案不符合规范，坚决判 0.0 分；\n"
            "   - 【错别字零容忍】：学科专有名词、术语出现错别字、音近字、形近字一律判 0.0 分（例如理化专有名词‘熔化’写成‘融化/溶化’判 0 分）；\n"
            "   - 【严禁脑补推测】：只认卷面上客观写出的文字，严禁猜测推断考生心理；\n"
            "   - 【矛盾作答一律0分】：若出现前后矛盾或写了两个相反选项（如‘增大或减小’），判 0.0 分；\n"
            f"   - 只有作答精准规范且包含关键采分点，判满分 {max_score} 分；若不规范、有错字或答错，一律判 0.0 分或按采分点扣分。"
        )
    elif strictness == "standard":
        strict_block = (
            "4. 【标准学术判分要求（常规考试标准）】：\n"
            "   - 允许公认严格等价的规范学术近义词，但严禁使用生活大白话或口语替代学科术语；\n"
            "   - 错别字或关键术语残缺扣除相应分数或判 0.0 分；\n"
            f"   - 核心采分点完整且表达规范，判满分 {max_score} 分；部分答对按比例给分；答错判 0.0 分。"
        )
    else:  # lenient
        strict_block = (
            "4. 【宽容理解判分要求（随堂练习标准）】：\n"
            f"   - 若学生最终作答的核心含义与参考答案一致，即可判满分 {max_score} 分；\n"
            "   - 容许轻微的生活化口语表达或非原则性笔误；答错判 0.0 分。"
        )

    custom_block = ""
    if custom_rules:
        custom_block = f"\n5. 【教师特别补充限制（最高优先级无条件执行）】：\n{custom_rules}\n"
        format_num = "6"
    else:
        format_num = "5"

    prompt_text = (
        f"你是一名国家级理科阅卷专家兼高精度手写字转录专家。请严格按两步对学生答题切片进行辨识与判分：\n\n"
        f"【第一步：视觉客观转写（严禁脑补）】\n"
        f"1. 仔细辨析图片中学生在横线上留下的真实手写墨迹：\n"
        f"   - 印刷体干扰排除：切片左右或上下边缘若切到了试卷原本印刷的题干字（如宋体/黑体字‘在’、‘处’、‘位于’、题号或括号横线），【绝对严禁】把印刷体当学生手写答案！必须且仅提取学生【手写】墨迹；若横线上无手写内容则判定为空白。\n"
        f"   - 划线与涂改过滤：若手写字中有划线删除（如横线、斜杠划去）、涂黑方块或橡皮擦除痕迹，【自动忽略被涂改的内容】，仅提取学生最终保留或订正的文字。\n"
        f"   - 如实转写一字不增：学生手写了什么就如实输出什么，【绝对严禁】根据参考答案擅自补齐前缀、后缀、遗漏字或脑补联想！\n"
        f"   - 模糊拒识：若字迹极度潦草、涂改重叠污损完全无法确凿辨识，请如实将识别文字标为‘[模糊]’，切勿强行猜测。\n\n"
        f"【第二步：对照参考答案与采分规则评分】\n"
        f"- 题号/题位：{label}\n"
        f"- 本空满分：{max_score} 分\n"
        f"- 标准/参考答案：{answers_str}\n"
        f"- 核心采分点：{keywords_str}\n\n"
        f"{induction_block}"
        f"{physics_block}"
        f"{type_block}"
        f"{strict_block}\n"
        f"{custom_block}"
        f"{format_num}. 【输出格式】：只输出一个 JSON 格式块，格式严格如下：\n"
        f"```json\n"
        f'{{\n'
        f'  "recognized_text": "学生手写最终有效文字（无手写填空白，字迹无法辨识填[模糊]）",\n'
        f'  "confidence": 识别置信度数值(0.0到1.0之间，字迹清晰确凿给0.95~1.0，有涂改或字迹较潦草给0.7~0.85，严重模糊给0.5以下),\n'
        f'  "score": 得分数值,\n'
        f'  "has_correction": true或false（表示是否有涂改/订正痕迹）,\n'
        f'  "reason": "简明评分理由（25字以内，写明给分或扣分依据）"\n'
        f'}}\n'
        f"```"
    )

    url = f"{endpoint}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": img_data_url}},
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
        "temperature": 0.0,
        "top_p": 0.1,
        "seed": 42,
        "max_tokens": 250,
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res_obj = json.loads(resp.read().decode("utf-8"))
            content = res_obj.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            parsed = _extract_json_from_llm_response(content)
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError('模型未返回有效 JSON 对象，请人工核对')
            if not isinstance(parsed.get('recognized_text'), str):
                raise ValueError('模型缺少有效识别文字，请人工核对')
            rec_text = parsed['recognized_text'].strip()
            raw_score = parsed.get('score')
            if isinstance(raw_score, bool) or raw_score is None:
                raise ValueError('模型未返回有效分数，请人工核对')
            score_val = float(raw_score)
            if not math.isfinite(score_val) or not 0 <= score_val <= max_score:
                raise ValueError('模型分数超出范围或不是有限数值，请人工核对')

            # 提取置信度与模糊状态
            raw_conf = parsed.get('confidence')
            try:
                confidence = float(raw_conf) if raw_conf is not None else 0.95
            except Exception:
                confidence = 0.95
            confidence = max(0.0, min(1.0, confidence))

            is_text_vague = (rec_text == '[模糊]' or '模糊' in rec_text or rec_text == '看不清' or '看不清' in rec_text)
            is_low_conf = (confidence < conf_threshold) or is_text_vague

            # 约束分数范围 [0, max_score]
            final_score = max(0.0, min(max_score, score_val))
            has_corr = bool(parsed.get("has_correction", False))
            reason = str(parsed.get("reason", "")).strip() or f"大模型评{final_score}分"
            needs_manual = False
            same_trend = False

            # 代码层严格模式与科学逻辑双重兜底仲裁：
            def _norm(s):
                return re.sub(r'[\s\u3000\.,!?;:，。！？；：、_—\(\)（）\-\+]+', '', str(s)).lower()

            norm_rec = _norm(rec_text)
            norm_exps = [_norm(x) for x in expected_answers if str(x).strip()]
            norm_kws = [_norm(k) for k in expected_keywords if str(k).strip()]

            # 1. 物理学科因果倒置一票否决（铁律，坚决纠偏）
            is_inverted, invert_reason = check_physics_causality(rec_text, answers_str)
            if is_inverted:
                final_score = 0.0
                reason = f"【因果颠倒判0分】：{invert_reason}"

            # 2. 选填题 vs 自由填空趋势题深度仲裁
            elif choice_fill and expected_answers:
                # 铁律：标准答案绝对优先，只要学生作答完全命中标准参考答案，坚决判满分！
                hit_expected_exact = any(norm_rec == exp for exp in norm_exps if exp)
                if hit_expected_exact:
                    final_score = max_score
                    reason = f"【标准答案命中】：作答‘{rec_text}’命中标准参考答案"
                else:
                    raw_cands = [w.strip() for w in re.split(r'[/,，、|\s]+|(?:\s*或\s*)', candidate_words) if w.strip()] if candidate_words else []
                    # 清洗选项前缀说明词，如“前两空选填”、“备选”等
                    cands_list = []
                    for c in raw_cands:
                        c_clean = re.sub(r'^(?:[前后上下第\d一二三四五六七八九十两\s]*空)?(?:选填|请选填|选择|备选)[：:]?\s*', '', c).strip()
                        if c_clean and not re.search(r'^(?:[前后上下第\d一二三四五六七八九十两\s]*空)?(?:选填|备选)$', c_clean):
                            if c_clean not in cands_list:
                                cands_list.append(c_clean)

                    # 自洽性校验：若候选词池根本不包含任何一个标准答案（例如多空题把前空的选项错挂到后空），
                    # 说明候选词配置无效/错配，此时自动熔断选填约束，回退自由作答评分！
                    cand_has_exp = any(any(_norm(c) == exp for exp in norm_exps) for c in cands_list)
                    if cands_list and not cand_has_exp:
                        cands_list = []  # 熔断无效候选词约束

                    if cands_list:
                        hit_cand = any(norm_rec == _norm(c) for c in cands_list)
                        if not hit_cand:
                            # 再次检查是否包含标准答案的核心语义（如“具有惯性”包含“惯性”）
                            if any(exp in norm_rec for exp in norm_exps if exp):
                                final_score = max_score
                                reason = f"【正确】：作答‘{rec_text}’包含标准参考答案"
                            else:
                                final_score = 0.0
                                reason = f"【选填题未选原词】：作答‘{rec_text}’不在限定选项（{'/'.join(cands_list)}）中，判0分"
                        elif not any(norm_rec == _norm(ans) for ans in expected_answers):
                            final_score = 0.0
                            reason = f"【选填题错误】：选填了错误选项‘{rec_text}’，判0分"
                        else:
                            final_score = max_score
                            reason = f"【选填题正确】：命中标准选项‘{rec_text}’"
                    else:
                        # 自由填空或候选词熔断后，按是否命中标准答案兜底
                        if any(exp in norm_rec for exp in norm_exps if exp):
                            final_score = max_score
                            reason = f"【正确】：作答‘{rec_text}’命中标准参考答案"
                        elif norm_exps and (norm_rec not in norm_exps):
                            final_score = 0.0
                            reason = f"【未命中答案】：识别文字‘{rec_text}’与标准答案不符，判0分"

            # 3. 自由填空变化趋势题同向放行 / 反向判0分
            elif not choice_fill and answers_str:
                is_trend, is_same, trend_dir = match_trend_direction(rec_text, answers_str)
                if is_trend:
                    if is_same:
                        same_trend = True
                        if final_score < max_score:
                            final_score = max_score
                            reason = f"【趋势同向给分】：作答‘{rec_text}’与标准答案变化方向一致，判满分"
                    else:
                        final_score = 0.0
                        reason = f"【趋势相反判0分】：作答‘{rec_text}’与标准答案变化趋势相反"

            # 4. 专有名词/唯一答案题客观字面强约束
            if unique_answer and expected_answers and final_score > 0.0:
                if norm_exps and (norm_rec not in norm_exps):
                    final_score = 0.0
                    reason = f"【唯一答案纠偏】：识别文字‘{rec_text}’与专有名词/唯一答案字面不符，系统自动纠偏判0分"

            # 5. 严格模式下的关键词采分点兜底
            elif strictness == "strict" and expected_keywords and final_score > 0.0 and not is_inverted and not same_trend:
                missing_kw = any(k not in norm_rec for k in norm_kws) if norm_kws else False
                if missing_kw:
                    final_score = 0.0
                    reason = f"【严格模式纠偏】：识别文字‘{rec_text}’缺失必备采分关键词，系统自动纠偏判0分"

            # 6. 置信度拒识与模糊退回人工机制（关键防误判机制）
            if is_low_conf:
                needs_manual = True
                if is_text_vague:
                    reason = f"【字迹模糊退回人工】：{reason}"
                else:
                    reason = f"【置信度偏低({confidence:.2f})退回人工】：{reason}"

            return {
                "success": True,
                "recognized_text": rec_text,
                "confidence": confidence,
                "score": final_score,
                "max_score": max_score,
                "has_correction": has_corr,
                "needs_manual": needs_manual,
                "reason": reason,
                "error": None,
                "model": model,
            }

    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}: {e.reason}"
        return {
            "success": False,
            "recognized_text": "",
            "confidence": 0.0,
            "score": 0.0,
            "max_score": max_score,
            "has_correction": False,
            "needs_manual": True,
            "reason": f"接口错误: {err_msg}",
            "error": err_msg,
            "model": model,
        }
    except Exception as e:
        return {
            "success": False,
            "recognized_text": "",
            "confidence": 0.0,
            "score": 0.0,
            "max_score": max_score,
            "has_correction": False,
            "needs_manual": True,
            "reason": f"请求异常: {e}",
            "error": str(e),
            "model": model,
        }


def batch_grade_tasks_concurrent(
    tasks: list[dict],
    get_rules_for_task_fn,
    progress_callback=None,
    cancel_check_fn=None,
    config: dict = None,
) -> list[tuple[dict, dict]]:
    """
    多线程并发批改主观题任务列表。
    :param tasks: 待批改的任务列表 [task_item, ...]
    :param get_rules_for_task_fn: 回调函数，输入 task_item，返回 (expected_answers, expected_keywords) 或 (expected_answers, expected_keywords, ocr_config)
    :param progress_callback: 回调函数 progress_callback(completed_count, total_count, last_task, last_result)
    :param cancel_check_fn: 回调函数，返回 True 表示用户已点击取消
    :param config: 大模型配置
    :return: 批改结果列表 [(task_item, result_dict), ...]
    """
    cfg = config or load_llm_config()
    concurrency = max(1, min(10, int(cfg.get("concurrency", 3))))
    total_count = len(tasks)
    results = []

    if total_count == 0:
        return results

    completed_count = 0

    def _worker(task_item):
        if cancel_check_fn and cancel_check_fn():
            return task_item, {"success": False, "error": "用户已取消", "reason": "用户取消", "needs_manual": True}
        rules = get_rules_for_task_fn(task_item)
        ocr_cfg = None
        if isinstance(rules, tuple):
            if len(rules) >= 3:
                ans, kw, ocr_cfg = rules[0], rules[1], rules[2]
            elif len(rules) == 2:
                ans, kw = rules[0], rules[1]
            else:
                ans, kw = [], []
        else:
            ans, kw = [], []
        if not ocr_cfg:
            ocr_cfg = task_item.get("ocr_config") or task_item.get("part", {}).get("ocr")
        res = grade_single_subjective_task(task_item, ans, kw, config=cfg, ocr_config=ocr_cfg)
        return task_item, res

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {executor.submit(_worker, t): t for t in tasks}

        for future in as_completed(future_map):
            if cancel_check_fn and cancel_check_fn():
                executor.shutdown(wait=False, cancel_futures=True)
                break

            try:
                task_item, res = future.result()
            except Exception as e:
                task_item = future_map[future]
                res = {
                    "success": False,
                    "score": 0.0,
                    "max_score": float(task_item.get("part", {}).get("score") or 1.0),
                    "recognized_text": "",
                    "has_correction": False,
                    "needs_manual": True,
                    "reason": f"并发处理异常: {e}",
                    "error": str(e),
                }

            results.append((task_item, res))
            completed_count += 1
            if progress_callback:
                progress_callback(completed_count, total_count, task_item, res)

    return results
