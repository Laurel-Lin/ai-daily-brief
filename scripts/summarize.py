from __future__ import annotations

import json
import logging
import os
from typing import Any

from utils import format_metric, truncate

LOGGER = logging.getLogger("ai_daily_brief")

BANNED_PHRASES = [
    "这是一个近期活跃的开源信号",
    "可能反映 Agent、AI 编程或模型应用的新需求",
    "可以观察它解决的具体工作流",
    "适合跟踪平台能力边界变化",
    "判断是否适合产品化为更稳定的工具能力",
]


def clean_text(text: str) -> str:
    for phrase in BANNED_PHRASES:
        text = text.replace(phrase, "")
    return text.strip()


def heat_evidence(candidate: dict[str, Any]) -> str:
    metrics = candidate.get("metrics", {})
    source_type = candidate.get("source_type")
    if source_type == "github":
        return "\n".join(
            [
                f"- 信号类型：{signal_label(candidate)}",
                f"- 创建时间：{format_metric(metrics.get('created_at'))}",
                f"- 仓库年龄：{format_metric(metrics.get('repo_age_days'))} 天",
                f"- stars：{format_metric(metrics.get('stars'))}",
                f"- forks：{format_metric(metrics.get('forks'))}",
                f"- open issues：{format_metric(metrics.get('open_issues'))}",
                f"- 24h star 增长：{format_metric(metrics.get('star_delta_24h'))}",
                f"- 7d star 增长：{format_metric(metrics.get('star_delta_7d'))}",
                f"- 7d fork 增长：{format_metric(metrics.get('fork_delta_7d'))}",
                f"- 最近 release：{format_metric(metrics.get('latest_release_at'))}",
                f"- 最近更新：{format_metric(metrics.get('recent_update'))}",
                f"- 为什么不是普通维护更新：{not_maintenance_reason(candidate)}",
            ]
        )
    if source_type == "hn":
        return "\n".join(
            [
                f"- points：{format_metric(metrics.get('points'))}",
                f"- comments：{format_metric(metrics.get('comments'))}",
                f"- 讨论主题：{candidate.get('title')}",
            ]
        )
    if source_type == "reddit":
        return "\n".join(
            [
                f"- upvotes：{format_metric(metrics.get('upvotes'))}",
                f"- comments：{format_metric(metrics.get('comments'))}",
                f"- 讨论主题：{candidate.get('title')}",
            ]
        )
    if source_type in {"huggingface", "modelscope"}:
        return "\n".join(
            [
                f"- likes：{format_metric(metrics.get('likes'))}",
                f"- downloads：{format_metric(metrics.get('downloads'))}",
                f"- discussion：{format_metric(metrics.get('discussion'))}",
                f"- 最近更新：{format_metric(metrics.get('recent_update'))}",
            ]
        )
    if str(source_type).startswith("x_"):
        return "\n".join(
            [
                f"- x_heat：{format_metric(metrics.get('x_heat'))}",
                f"- likes：{format_metric(metrics.get('like_count'))}",
                f"- reposts：{format_metric(metrics.get('repost_count'))}",
                f"- replies：{format_metric(metrics.get('reply_count'))}",
                f"- quotes：{format_metric(metrics.get('quote_count'))}",
            ]
        )
    if source_type in {"chinese_media", "chinese_community"}:
        return "\n".join(
            [
                f"- 来源：{candidate.get('source')}",
                f"- likes：{format_metric(metrics.get('likes'))}",
                f"- collects：{format_metric(metrics.get('collects'))}",
                f"- comments：{format_metric(metrics.get('comments'))}",
            ]
        )
    return "\n".join(
        [
            f"- 来源：{candidate.get('source')}",
            "- 是否官方发布：是",
            "- 公开热度数据未知",
        ]
    )


def inline_heat(candidate: dict[str, Any]) -> str:
    metrics = candidate.get("metrics", {})
    if candidate.get("source_type") == "github":
        return f"{format_metric(metrics.get('stars'))} stars / {format_metric(metrics.get('forks'))} forks"
    if candidate.get("source_type") == "hn":
        return f"{format_metric(metrics.get('points'))} points / {format_metric(metrics.get('comments'))} comments"
    if candidate.get("source_type") == "reddit":
        return f"{format_metric(metrics.get('upvotes'))} upvotes / {format_metric(metrics.get('comments'))} comments"
    if candidate.get("source_type") in {"huggingface", "modelscope"}:
        return f"{format_metric(metrics.get('likes'))} likes / {format_metric(metrics.get('downloads'))} downloads"
    if str(candidate.get("source_type", "")).startswith("x_"):
        return f"x_heat {format_metric(metrics.get('x_heat'))}"
    if candidate.get("source_type") in {"chinese_media", "chinese_community"}:
        return f"{format_metric(metrics.get('likes'))} likes / {format_metric(metrics.get('comments'))} comments"
    return "公开热度数据未知"


def signal_label(candidate: dict[str, Any]) -> str:
    source_type = candidate.get("source_type")
    metrics = candidate.get("metrics", {})
    if source_type == "github":
        mapping = {
            "new_project": "新项目",
            "fast_growing": "快速增长",
            "major_release": "重大版本",
            "mature_reference": "成熟基础设施，仅限每日最多 1 条",
            "maintenance_update": "普通维护更新",
        }
        return mapping.get(metrics.get("signal_type"), "开源信号")
    if source_type == "official":
        return "官方发布"
    if source_type in {"hn", "reddit"} or str(source_type).startswith("x_"):
        return "高热讨论"
    if source_type in {"chinese_media", "chinese_community"}:
        return "中文社区信号"
    if source_type in {"huggingface", "modelscope"}:
        return "模型生态"
    return "用户需求"


def not_maintenance_reason(candidate: dict[str, Any]) -> str:
    metrics = candidate.get("metrics", {})
    signal_type = metrics.get("signal_type")
    if signal_type == "new_project":
        return "仓库创建时间很近，属于新项目发现，不是老项目 pushed_at 维护。"
    if signal_type == "fast_growing":
        return "存在明确 star/fork 增长信号，不只是 pushed_at 更新。"
    if signal_type == "major_release":
        return "存在近期 release 或 release 文案命中重要版本关键词。"
    if signal_type == "mature_reference":
        return "作为成熟基础设施候选被每日限额保留，需要继续结合外部讨论验证。"
    return "无法证明不是普通维护更新。"


def text_blob(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('title', '')} {candidate.get('summary', '')} {' '.join(candidate.get('tags', []))}".lower()


def classify(candidate: dict[str, Any]) -> str:
    text = text_blob(candidate)
    source_type = candidate.get("source_type")
    if source_type in {"huggingface", "modelscope"}:
        return "模型生态"
    if source_type in {"hn", "reddit", "whitelist_social"}:
        return "用户反馈"
    if str(source_type).startswith("x_"):
        return "用户反馈" if source_type != "x_official" else "产品更新"
    if source_type in {"chinese_media", "chinese_community"}:
        return "国内社区"
    if "mcp" in text:
        return "Agent"
    if any(term in text for term in ["coding", "code", "cli", "cursor", "claude code", "copilot", "编程"]):
        return "AI 编程"
    if any(term in text for term in ["rag", "retrieval", "search", "document", "semantic"]):
        return "RAG"
    if any(term in text for term in ["agent", "workflow", "orchestration", "tool use", "memory"]):
        return "Agent"
    if any(term in text for term in ["multimodal", "vision", "image", "video", "多模态"]):
        return "模型生态"
    if source_type == "github":
        return "开源工具"
    if source_type == "official":
        return "产品更新"
    return "AI 产品"


def priority(candidate: dict[str, Any], index: int) -> str:
    score = candidate.get("score", 0)
    if index == 0 or score >= 88:
        return "P0"
    if score >= 82:
        return "P1"
    return "P2"


def tracking_value(candidate: dict[str, Any]) -> tuple[str, str]:
    score = candidate.get("score", 0)
    source_type = candidate.get("source_type")
    if score >= 88:
        return "高", "数据、场景和关注方向都比较明确，后续变化可能影响工具选型或产品设计。"
    if source_type == "official" or score >= 80:
        return "中", "值得观察后续采用情况，但还需要更多用户反馈或实际案例验证。"
    return "低", "信息量有限，适合作为背景信号，不建议投入太多跟踪精力。"


def infer_tool_role(candidate: dict[str, Any]) -> str:
    text = text_blob(candidate)
    if "mcp" in text:
        return "把外部工具接入 Agent 的 MCP 工具"
    if "cli" in text or "coding" in text or "code" in text:
        return "面向开发者的 AI 编程工具"
    if ("rag" in text or "retrieval" in text or "search" in text or "document" in text) and (
        "agent" in text or "workflow" in text or "orchestration" in text
    ):
        return "知识检索和 Agent 编排框架"
    if "rag" in text or "retrieval" in text or "search" in text or "document" in text:
        return "知识检索和 RAG 应用框架"
    if "gateway" in text or "provider" in text:
        return "模型网关"
    if "prompt" in text or "red teaming" in text or "eval" in text or "test" in text:
        return "AI 应用评测与安全测试工具"
    if "agent" in text or "workflow" in text or "orchestration" in text:
        return "Agent 工作流编排工具"
    if "multimodal" in text:
        return "多模态 AI 应用工具"
    return "AI 开发工具"


def chinese_capability(role: str) -> str:
    if "MCP" in role:
        return "把模型、搜索、多模态或内部服务包装成 Agent 可以调用的标准工具，减少不同工具之间的接入成本。"
    if "AI 编程" in role:
        return "把 AI 能力放进终端、编辑器或代码仓库流程里，帮助开发者完成生成、修改、调试和自动化操作。"
    if "Agent 编排" in role:
        return "提供检索、上下文组织、任务路由和工作流编排能力，让应用能处理多步骤、带资料上下文的任务。"
    if "RAG" in role:
        return "把文档、知识库或多模态资料转成可检索、可问答的应用能力，重点是召回质量和上下文组织。"
    if "模型网关" in role:
        return "把多个模型供应商统一到一个调用入口，并处理路由、降级、成本和调用稳定性。"
    if "评测" in role:
        return "帮助团队测试提示词、Agent 和 RAG 应用，提前发现质量、安全或稳定性问题。"
    if "Agent 工作流" in role:
        return "把复杂任务拆成步骤，并协调工具、记忆、状态和执行结果，让 AI 能推进完整流程。"
    if "多模态" in role:
        return "处理文本、图像、视频或其他模态之间的输入输出，让 AI 应用覆盖更多真实场景。"
    return "把 AI 能力封装成开发者或业务团队可直接接入的工具。"


def explain_what(candidate: dict[str, Any]) -> str:
    title = candidate.get("title", "")
    source_type = candidate.get("source_type")
    source = candidate.get("source", "")
    summary = clean_text(candidate.get("summary", ""))
    role = infer_tool_role(candidate)

    if source_type == "github":
        if summary:
            return f"{title} 是一个开源的{role}。它面向正在搭建 AI 应用、Agent 或开发者工具的团队，{chinese_capability(role)}"
        return f"{title} 是一个开源的{role}。当前可用信息主要来自仓库名称、标签和 GitHub 指标。"
    if source_type in {"huggingface", "modelscope"}:
        return f"{title} 是 {source} 上的模型或模型资源。它的价值需要结合用途、下载量、点赞和讨论数据判断。"
    if source_type in {"hn", "reddit", "whitelist_social"}:
        return f"{title} 是社区正在讨论的 AI 话题。它的重点不只是事件本身，而是评论里暴露出的支持、吐槽和分歧。"
    if source_type == "official":
        if summary:
            return f"{title} 是 {source} 发布的官方更新。它说明该平台正在把 AI 能力推进到更具体的产品环节：{truncate(summary, 96)}"
        return f"{title} 是 {source} 发布的官方更新。它值得关注的是平台把能力放进了哪个真实使用环节。"
    return f"{title} 是一条 AI 相关信息。当前信息有限，需要阅读原文确认具体价值。"


def solve_problem(candidate: dict[str, Any]) -> str:
    item_type = classify(candidate)
    text = text_blob(candidate)
    if item_type == "AI 编程":
        return "它解决的是开发者在终端、编辑器或代码仓库里调用 AI 的问题，重点是减少上下文切换，让生成、修改、调试代码更贴近真实工程流程。"
    if item_type == "RAG":
        return "它解决的是把文档、知识库或非结构化资料变成可检索、可问答、可嵌入应用的问题，适合企业知识助手和垂直 Agent。"
    if item_type == "Agent":
        if "mcp" in text:
            return "它解决的是 Agent 如何安全、稳定地连接外部工具、部署环境和开发流程的问题。"
        return "它解决的是多步骤任务如何被拆解、编排和执行的问题，让 AI 不只回答问题，而是能推进一个工作流。"
    if item_type == "模型生态":
        return "它解决的是模型能力选择问题：开发者需要知道这个模型是否适合本地部署、多模态任务、中文场景或低成本推理。"
    if item_type == "用户反馈":
        return "它解决的是需求验证问题：通过真实讨论看用户到底在买单、抱怨或争论什么。"
    if item_type == "产品更新":
        return "它解决的是平台能力落地问题：官方把 AI 能力放到具体产品入口后，开发者和用户的默认工作方式会随之改变。"
    return "它解决的是 AI 能力如何进入具体业务或开发流程的问题，需要结合原文判断是否有明确使用场景。"


def why_today(candidate: dict[str, Any]) -> str:
    item_type = classify(candidate)
    metrics = candidate.get("metrics", {})
    stars = metrics.get("stars") or 0
    forks = metrics.get("forks") or 0
    updated = metrics.get("recent_update") or candidate.get("published_at")
    source_type = candidate.get("source_type")
    if source_type == "github":
        signal = signal_label(candidate)
        if metrics.get("signal_type") == "new_project":
            return f"它属于{signal}：创建时间是 {format_metric(metrics.get('created_at'))}，不是老项目例行更新；当前 stars 为 {format_metric(metrics.get('stars'))}，值得观察能否继续增长。"
        if metrics.get("signal_type") == "fast_growing":
            return f"它属于{signal}：24h star 增长 {format_metric(metrics.get('star_delta_24h'))}，7d star 增长 {format_metric(metrics.get('star_delta_7d'))}，热度来自新增关注而不是总 stars。"
        if metrics.get("signal_type") == "major_release":
            return f"它属于{signal}：最近 release 是 {format_metric(metrics.get('latest_release_at'))}，更接近产品/能力发布，不是普通 pushed_at。"
        if stars >= 10000:
            return f"它已经有 {stars} stars 和 {forks} forks，同时最近更新在 {format_metric(updated)}，说明不是冷门概念项目，而是有较大开发者关注度的工具。"
        role = infer_tool_role(candidate)
        return f"它在 {format_metric(updated)} 仍有更新，虽然体量还不算大，但把“{role}”做成了可直接试用的开源实现，适合判断这个场景是否有真实开发者需求。"
    if source_type == "official":
        return "这是官方发布，不依赖二手解读；它的价值在于观察平台把 AI 能力嵌进了哪个产品动作或开发环节。"
    if source_type in {"hn", "reddit", "whitelist_social"}:
        return f"它有明确讨论指标：{inline_heat(candidate)}。讨论型内容的价值在于暴露真实支持点和反对意见。"
    if str(source_type).startswith("x_"):
        return f"它来自白名单 X 账号，公开互动热度为 {inline_heat(candidate)}；如果内容能说明产品变化或用户需求，就值得作为社交信号跟踪。"
    if source_type in {"chinese_media", "chinese_community"}:
        return "它来自中文社区/中文媒体源，价值在于补足国内用户需求、产品机会和信息差，而不是只看英文技术圈。"
    if source_type in {"huggingface", "modelscope"}:
        return f"模型热度依据是 {inline_heat(candidate)}。如果热度继续上升，说明它可能正在进入更多实验或产品原型。"
    return "它与今天的 AI 产品和开发者工具主题相关，但还需要更多外部信号验证。"


def inspiration(candidate: dict[str, Any]) -> str:
    item_type = classify(candidate)
    if item_type == "AI 编程":
        return "对 AI 编程产品的启发是：用户要的不是聊天窗口，而是能理解项目、调用工具、进入终端和 CI 流程的协作层。"
    if item_type == "RAG":
        return "对 AI 产品的启发是：知识检索仍是很多 Agent 的底座，差异化会出现在文档解析、权限、评测和更新机制上。"
    if item_type == "Agent":
        return "对 Agent 工作流的启发是：真正有价值的能力会围绕工具连接、任务状态、长期执行和可调试性展开。"
    if item_type == "模型生态":
        return "对模型生态的启发是：模型是否值得用，不只看榜单，还要看部署成本、任务适配和社区反馈。"
    if item_type == "用户反馈":
        return "对用户需求的启发是：评论区的抱怨、迁移理由和失败案例，往往比发布稿更接近真实需求。"
    if item_type == "国内社区":
        return "对 AI 产品的启发是：中文社区更容易暴露本土用户的部署门槛、价格敏感点、工具选择和内容平台需求。"
    return "对 AI 产品的启发是：平台更新只有进入具体工作流，才会变成用户能感知的效率变化。"


def build_brief(candidate: dict[str, Any], index: int) -> dict[str, str]:
    track, track_reason = tracking_value(candidate)
    return {
        "priority": priority(candidate, index),
        "title": candidate.get("title", ""),
        "type": classify(candidate),
        "what": clean_text(explain_what(candidate)),
        "problem": clean_text(solve_problem(candidate)),
        "why": clean_text(why_today(candidate)),
        "inspiration": clean_text(inspiration(candidate)),
        "tracking": track,
        "tracking_reason": track_reason,
        "signal": signal_label(candidate),
        "source": candidate.get("source", ""),
    }


def maybe_enhance_with_openai(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not candidates:
        if not api_key:
            LOGGER.info("OPENAI_API_KEY not set; use rule-based brief rendering")
        return candidates

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        payload = [
            {
                "id": item["id"],
                "title": item["title"],
                "source": item["source"],
                "source_type": item.get("source_type"),
                "summary": item.get("summary", ""),
                "tags": item.get("tags", []),
                "metrics": item.get("metrics", {}),
                "score": item.get("score"),
            }
            for item in candidates
        ]
        prompt = (
            "你是 AI 产品、Agent 和 AI 编程方向的中文日报编辑。只基于输入信息，不编造数据。"
            "为每条内容输出 JSON 数组，字段包括 id, summary_cn, why_important, inspiration。"
            "必须具体说明是什么、解决什么问题、为什么值得看；禁止空泛套话。输入："
            + json.dumps(payload, ensure_ascii=False)
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content.removeprefix("json").strip()
        enhanced = json.loads(content)
        by_id = {item.get("id"): item for item in enhanced if isinstance(item, dict)}
        for candidate in candidates:
            update = by_id.get(candidate["id"])
            if not update:
                continue
            candidate["summary"] = clean_text(update.get("summary_cn") or candidate.get("summary") or "")
            candidate["why_important"] = clean_text(update.get("why_important") or candidate.get("why_important") or "")
            candidate["inspiration"] = clean_text(update.get("inspiration") or candidate.get("inspiration") or "")
        LOGGER.info("OpenAI brief enhancement succeeded")
    except Exception:
        LOGGER.exception("OpenAI brief enhancement failed; fallback to rule-based rendering")
    return candidates


def today_one_liner(candidates: list[dict[str, Any]]) -> str:
    types = [classify(item) for item in candidates[:5]]
    if "Agent" in types and "AI 编程" in types:
        return "今天最值得关注的是 Agent 能力继续向开发、部署和调试环节渗透，AI 工具正在从问答入口变成工程协作层。"
    if "RAG" in types and "Agent" in types:
        return "今天的主线是 Agent 和知识检索继续合流，产品竞争点正在转向能否稳定处理真实业务上下文。"
    if "AI 编程" in types:
        return "今天的重点是 AI 编程工具继续贴近终端、仓库和自动化流程，开发者体验正在成为模型之外的竞争点。"
    if "模型生态" in types:
        return "今天的重点是模型生态的可用性信号，值得关注哪些模型真正进入实验、部署和产品原型。"
    if candidates:
        return "今天的有效信号集中在开发者工具和平台更新，重点不是单个标题，而是它们进入了哪些真实工作流。"
    return "今天没有足够高质量内容进入精选，宁可留空，也不把普通更新包装成趋势。"


def split_sections(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    main = candidates[:5]
    main_ids = {item.get("id") for item in main}
    rest = [item for item in candidates[5:] if item.get("id") not in main_ids]
    return {
        "main": main,
        "github": [item for item in rest if item.get("source_type") == "github"][:3],
        "models": [item for item in rest if item.get("source_type") in {"huggingface", "modelscope"}][:3],
        "social": [
            item
            for item in rest
            if item.get("source_type") in {"hn", "reddit", "whitelist_social"}
            or str(item.get("source_type", "")).startswith("x_")
        ][:3],
        "chinese": [item for item in rest if item.get("source_type") in {"chinese_media", "chinese_community"}][:3],
    }


def build_judgments(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    types = {classify(item) for item in candidates}
    judgments: list[dict[str, str]] = []
    if "AI 编程" in types:
        judgments.append(
            {
                "判断": "AI 编程工具的竞争点正在从代码生成转向工程入口控制。",
                "依据": "入选内容里出现 CLI、仓库工具或开发流程相关项目，热度数据主要来自 GitHub。",
                "意义": "产品设计要优先考虑项目上下文、终端操作、权限边界和可回滚的执行链路。",
            }
        )
    if "Agent" in types:
        judgments.append(
            {
                "判断": "Agent 的落地价值越来越依赖工具连接和任务编排，而不是单次回答质量。",
                "依据": "MCP、工作流、orchestration、tool use 等关键词反复出现在候选项目和官方更新中。",
                "意义": "Agent 产品需要把状态、工具调用、错误恢复和日志解释做成核心体验。",
            }
        )
    if "RAG" in types:
        judgments.append(
            {
                "判断": "RAG 仍然是企业 AI 应用的基础设施问题，不是已经解决的老话题。",
                "依据": "进入精选的项目仍在围绕文档检索、上下文工程和生产可用性做增量。",
                "意义": "AI 产品要把文档解析、权限、召回质量和评测闭环一起设计，单纯接向量库不够。",
            }
        )
    if "产品更新" in types:
        judgments.append(
            {
                "判断": "平台方正在把 AI 能力塞进更靠近交付的环节。",
                "依据": "官方发布内容进入精选，说明相关能力已经从概念演示走向产品入口。",
                "意义": "开发者工具和 Agent 产品要关注这些平台入口，否则容易被默认工作流吸走用户。",
            }
        )
    fallback = [
        {
            "判断": "今天有效信号偏少时，过滤质量比数量更重要。",
            "依据": "大量候选没有进入日期窗口、缺少具体场景或缺少足够热度依据。",
            "意义": "日报应该保留判断密度，避免把低信息量更新推给用户。",
        },
        {
            "判断": "下一步最该补强的是社区反馈源。",
            "依据": "如果 HN、Reddit 或 X 没有稳定抓取，就很难看到用户支持点和吐槽点。",
            "意义": "产品机会往往藏在真实讨论里，而不是官方标题或仓库指标里。",
        },
        {
            "判断": "开源热度只能说明值得看，不能直接说明值得用。",
            "依据": "stars、forks 和更新时间能证明关注度，但不能替代部署体验、文档质量和维护稳定性。",
            "意义": "后续评估要加入 README 质量、issue 内容和实际试用反馈。",
        },
    ]
    for item in fallback:
        if len(judgments) >= 3:
            break
        if item["判断"] not in {existing["判断"] for existing in judgments}:
            judgments.append(item)
    return judgments[:3]


def render_overview_table(main: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 优先级 | 内容 | 来源 | 信号类型 | 它是什么 | 为什么值得看 | 追踪价值 |",
        "|---|---|---|---|---|---|---|",
    ]
    if not main:
        lines.append("| - | 今日无精选 | - | - | 今日未发现足够高质量内容 | 保持空位比硬凑更有价值 | 低 |")
        return lines
    for index, candidate in enumerate(main):
        brief = build_brief(candidate, index)
        lines.append(
            "| {priority} | {title} | {source} | {signal} | {what} | {why} | {tracking} |".format(
                priority=brief["priority"],
                title=brief["title"].replace("|", "/"),
                source=brief["source"].replace("|", "/"),
                signal=brief["signal"],
                what=truncate(brief["what"], 70).replace("|", "/"),
                why=truncate(brief["why"], 70).replace("|", "/"),
                tracking=brief["tracking"],
            )
        )
    return lines


def render_card(candidate: dict[str, Any], index: int) -> str:
    brief = build_brief(candidate, index)
    return "\n".join(
        [
            f"### {brief['priority']}｜{brief['title']}",
            "",
            f"标签：{brief['type']}",
            f"信号类型：{brief['signal']}",
            "",
            "它是什么：",
            brief["what"],
            "",
            "解决什么问题：",
            brief["problem"],
            "",
            "热度依据：",
            heat_evidence(candidate),
            "",
            "为什么今天值得看：",
            brief["why"],
            "",
            "对我的启发：",
            brief["inspiration"],
            "",
            "适合继续追踪吗：",
            f"{brief['tracking']}。{brief['tracking_reason']}",
            "",
            "原文链接：",
            candidate.get("url", ""),
        ]
    )


def render_markdown(date_str: str, candidates: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    candidates = maybe_enhance_with_openai(candidates)
    sections = split_sections(candidates)
    judgments = build_judgments(candidates)
    lines = [f"# AI 精选日报 {date_str}", ""]

    lines.extend(["## 今日一句话", "", today_one_liner(sections["main"]), ""])

    lines.extend(["## 今日速览", ""])
    lines.extend(render_overview_table(sections["main"]))
    lines.append("")

    lines.extend(["## 重点卡片", ""])
    if sections["main"]:
        for index, candidate in enumerate(sections["main"]):
            lines.append(render_card(candidate, index))
            lines.append("")
    else:
        lines.extend(["今日未发现足够高质量内容。", ""])

    supplement_lines: list[str] = []
    if sections["github"]:
        supplement_lines.extend(["### GitHub 开源信号", ""])
        for candidate in sections["github"]:
            brief = build_brief(candidate, 5)
            supplement_lines.extend(
                [
                    f"- 项目名：{candidate.get('title')}",
                    f"- 做什么：{truncate(brief['what'], 90)}",
                    f"- 为什么暂时只备注：没有进入前 5，但 {inline_heat(candidate)}，仍可作为方向观察。",
                    "",
                ]
            )
    if sections["models"]:
        supplement_lines.extend(["### 模型生态", ""])
        for candidate in sections["models"]:
            brief = build_brief(candidate, 5)
            supplement_lines.extend(
                [
                    f"- 模型名：{candidate.get('title')}",
                    f"- 做什么：{truncate(brief['what'], 90)}",
                    f"- 为什么暂时只备注：模型热度依据为 {inline_heat(candidate)}，需要继续观察实际采用。",
                    "",
                ]
            )
    if sections["social"]:
        supplement_lines.extend(["### 高热社交信号", ""])
        for candidate in sections["social"]:
            supplement_lines.extend(
                [
                    f"- 内容：{candidate.get('title')}",
                    f"- 支持点 / 吐槽点 / 争议点：当前只拿到公开热度指标 {inline_heat(candidate)}，需要进一步读取评论后才能总结真实观点。",
                    f"- 链接：{candidate.get('url')}",
                    "",
                ]
            )

    supplement_lines.extend(["### 国内社区信号", ""])
    if sections["chinese"]:
        for candidate in sections["chinese"]:
            supplement_lines.extend(
                [
                    f"- 内容：{candidate.get('title')}",
                    f"- 用户在讨论什么：{truncate(candidate.get('summary', ''), 80)}",
                    "- 真实需求是什么：需要从原文或手动 note 里确认具体需求，系统不会编造。",
                    "- 类型：中文社区信号",
                    f"- 链接：{candidate.get('url')}",
                    "",
                ]
            )
    else:
        supplement_lines.extend(["今日未发现足够高质量的中文社区信号。", ""])
    lines.extend(["## 分类补充", ""])
    lines.extend(supplement_lines)

    lines.extend(["## 今日判断", ""])
    for index, judgment in enumerate(judgments, start=1):
        lines.extend(
            [
                f"{index}. 判断：{judgment['判断']}",
                f"   依据：{judgment['依据']}",
                f"   意义：{judgment['意义']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 候选内容过滤说明",
            "",
            f"- 今日抓取候选数量：{stats.get('raw_candidates', 0)}",
            f"- 进入评分数量：{stats.get('scored_candidates', 0)}",
            f"- 最终入选数量：{stats.get('selected_candidates', 0)}",
            f"- 被过滤的主要原因：{'；'.join(stats.get('main_filter_reasons', []))}",
            "",
        ]
    )
    return clean_text("\n".join(lines)) + "\n"


def render_wechat_summary(date_str: str, candidates: list[dict[str, Any]]) -> tuple[str, str]:
    main = candidates[:3]
    judgments = build_judgments(candidates)
    title = f"AI 精选日报 {date_str}"
    lines = [title, "", "今日一句话：", today_one_liner(candidates[:5]), "", f"今日最值得看：{len(main)} 条", ""]
    for index, candidate in enumerate(main, start=1):
        brief = build_brief(candidate, index - 1)
        lines.extend(
            [
                f"{index}. {brief['title']}",
                f"类型：{brief['type']}",
                f"它是什么：{truncate(brief['what'], 80)}",
                f"为什么值得看：{truncate(brief['why'], 80)}",
                f"追踪价值：{brief['tracking']}",
                "",
            ]
        )
    lines.append("今日判断：")
    for judgment in judgments:
        lines.append(f"- {judgment['判断']}")
    lines.extend(["", "完整版：", f"digests/{date_str}.md"])
    return title, clean_text("\n".join(lines))
