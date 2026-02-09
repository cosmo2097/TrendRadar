# coding=utf-8
"""
Briefing Server 工具模块
"""
import re
from typing import List, Dict, Tuple, Union

def _parse_word(word: str) -> Dict:
    """
    解析单个词，识别是否为正则表达式，支持显示名称
    (复制自 trendradar.core.frequency)
    """
    display_name = None

    # 1. 优先处理显示名称 (=>)
    if '=>' in word:
        parts = re.split(r'\s*=>\s*', word, 1)
        word_config = parts[0].strip()
        if len(parts) > 1 and parts[1].strip():
            display_name = parts[1].strip()
    else:
        word_config = word.strip()

    # 2. 解析正则表达式
    regex_match = re.match(r'^/(.+)/[a-z]*$', word_config)

    if regex_match:
        pattern_str = regex_match.group(1)
        try:
            pattern = re.compile(pattern_str, re.IGNORECASE)
            
            return {
                "word": pattern_str,
                "is_regex": True,
                "pattern": pattern,
                "display_name": display_name,
            }
        except re.error as e:
            pass

    return {
        "word": word_config, 
        "is_regex": False, 
        "pattern": None, 
        "display_name": display_name
    }

def parse_frequency_rules(
    rule_strings: List[str],
) -> Tuple[List[Dict], List[Dict], List[str]]:
    """
    解析频率词规则列表（内存处理）
    """
    processed_groups = []
    filter_words = []
    global_filters = []

    current_section = "WORD_GROUPS"

    # 预处理
    full_content = "\n\n".join(rule_strings)
    word_groups = [group.strip() for group in full_content.split("\n\n") if group.strip()]

    for group in word_groups:
        lines = [line.strip() for line in group.split("\n") if line.strip() and not line.strip().startswith("#")]

        if not lines:
            continue

        if lines[0].startswith("[") and lines[0].endswith("]"):
            section_name = lines[0][1:-1].upper()
            if section_name in ("GLOBAL_FILTER", "WORD_GROUPS"):
                current_section = section_name
                lines = lines[1:]

        if current_section == "GLOBAL_FILTER":
            for line in lines:
                if line.startswith(("!", "+", "@")):
                    continue
                if line:
                    global_filters.append(line)
            continue

        words = lines
        group_alias = None

        if words and words[0].startswith("[") and words[0].endswith("]"):
            potential_alias = words[0][1:-1].strip()
            if potential_alias.upper() not in ("GLOBAL_FILTER", "WORD_GROUPS"):
                group_alias = potential_alias
                words = words[1:]

        group_required_words = []
        group_normal_words = []
        group_max_count = 0

        for word in words:
            if word.startswith("@"):
                try:
                    count = int(word[1:])
                    if count > 0:
                        group_max_count = count
                except (ValueError, IndexError):
                    pass
            elif word.startswith("!"):
                filter_word = word[1:]
                parsed = _parse_word(filter_word)
                filter_words.append(parsed)
            elif word.startswith("+"):
                req_word = word[1:]
                group_required_words.append(_parse_word(req_word))
            else:
                group_normal_words.append(_parse_word(word))

        if group_required_words or group_normal_words:
            if group_normal_words:
                group_key = " ".join(w["word"] for w in group_normal_words)
            else:
                group_key = " ".join(w["word"] for w in group_required_words)

            if group_alias:
                display_name = group_alias
            else:
                all_words = group_normal_words + group_required_words
                display_parts = []
                for w in all_words:
                    part = w.get("display_name") or w["word"]
                    display_parts.append(part)
                display_name = " / ".join(display_parts) if display_parts else None

            processed_groups.append(
                {
                    "required": group_required_words,
                    "normal": group_normal_words,
                    "group_key": group_key,
                    "display_name": display_name,
                    "max_count": group_max_count,
                }
            )

    return processed_groups, filter_words, global_filters
