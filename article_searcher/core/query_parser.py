"""
高级搜索语法解析器（纯函数，便于单元测试）

支持语法（详见架构设计 §8③）：
  tag:A            按标签 A 过滤（可多个，取交集）
  path:x           按路径/文件名过滤（先 fnmatch 通配，再退化为大小写不敏感子串）
  -排除词          排除含该词的文档（内容级后过滤；-\"短语\" 排除短语）
  "精确短语"        作为语义强调短语（同时进入 clean_query 参与检索）

增量（P1-1）扩展：
  - 支持 AND / OR / NOT 与括号分组的布尔表达式，并同步构建布尔 AST（BoolExpr）。
  - ParsedQuery 新增 expr / has_boolean 字段（带默认值，向后兼容旧调用与旧测试）。
  - 新增 build_tag_filter_parsed / combine_parsed 辅助，供左栏多选筛选接入。

解析失败（如引号未闭合）→ is_valid=False、给出 warn 提示、
clean_query 回退为原始输入（降级为普通搜索），绝不崩溃。
"""

from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------- #
# 布尔表达式 AST（P1-1 新增）
# --------------------------------------------------------------------------- #
class BoolExpr:
    """布尔表达式抽象基类（普通类即可）。"""
    pass


@dataclass
class TagNode(BoolExpr):
    tag: str
    negated: bool = False


@dataclass
class PathNode(BoolExpr):
    pattern: str
    negated: bool = False


@dataclass
class TermNode(BoolExpr):
    """检索词（普通词 / 短语）。"""
    text: str
    negated: bool = False


@dataclass
class NotNode(BoolExpr):
    child: BoolExpr


@dataclass
class AndNode(BoolExpr):
    children: List[BoolExpr]


@dataclass
class OrNode(BoolExpr):
    children: List[BoolExpr]


# --------------------------------------------------------------------------- #
# ParsedQuery（向后兼容，新增字段全部带默认值）
# --------------------------------------------------------------------------- #
@dataclass
class ParsedQuery:
    clean_query: str                       # 喂给 embedding / BM25 的纯文本（去掉指令词）
    tag_filters: List[str] = field(default_factory=list)    # tag:A tag:B → 交集
    path_filters: List[str] = field(default_factory=list)   # path:xxx
    exclude_terms: List[str] = field(default_factory=list)  # -词 / -"短语"
    phrase: str = ""                       # 首个 "精确短语"（用于语义强调）
    is_valid: bool = True                  # 引号未闭合等 → False，降级普通搜索
    warn: str = ""                         # 友好提示文案
    # —— 增量（P1-1）新增，全部带默认值，不破坏旧调用/旧测试 ——
    expr: Optional[BoolExpr] = None        # 完整布尔 AST
    has_boolean: bool = False              # 仅当含 OR / NOT / 括号 / -tag: / -path: 时为 True


def parse_query(raw: str) -> ParsedQuery:
    """解析高级搜索语法。

    返回 ParsedQuery。任何异常都会被吞掉并返回降级结果，保证调用方不崩溃。

    旧逻辑（填充 clean_query/tag_filters/path_filters/exclude_terms/phrase/
    is_valid/warn）逐字保留；同步构建 expr 布尔树（仅当含 OR/NOT/括号/-tag:/
    -path: 时 has_boolean=True；文本级 -排除词 不置 has_boolean，走旧 exclude_terms）。
    """
    raw = raw or ""
    tag_filters: List[str] = []
    path_filters: List[str] = []
    exclude_terms: List[str] = []
    phrase = ""
    is_valid = True
    warn = ""
    clean_parts: List[str] = []

    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]

        # 跳过空白（作为 clean_query 的分隔符）
        if c.isspace():
            i += 1
            continue

        # === tag:A ===
        if raw.startswith("tag:", i):
            j = i + 4
            k = j
            while k < n and not raw[k].isspace():
                k += 1
            val = raw[j:k]
            if val:
                tag_filters.append(val)
            i = k
            continue

        # === path:x ===
        if raw.startswith("path:", i):
            j = i + 5
            k = j
            while k < n and not raw[k].isspace():
                k += 1
            val = raw[j:k]
            if val:
                path_filters.append(val)
            i = k
            continue

        # === -排除词 / -"排除短语" ===
        if c == "-":
            j = i + 1
            if j < n and raw[j] == '"':
                # 排除短语：-"深度学习"
                k = j + 1
                closed = False
                while k < n:
                    if raw[k] == '"':
                        closed = True
                        break
                    k += 1
                if not closed:
                    is_valid = False
                    warn = "引号未闭合，已按普通搜索执行"
                    clean_parts.append(raw[i:])
                    i = n
                    break
                term = raw[j + 1:k]
                if term:
                    exclude_terms.append(term)
                i = k + 1
                continue
            else:
                k = j
                while k < n and not raw[k].isspace():
                    k += 1
                term = raw[j:k]
                if term:
                    exclude_terms.append(term)
                i = k
                continue

        # === "精确短语" ===
        if c == '"':
            j = i + 1
            k = j
            closed = False
            while k < n:
                if raw[k] == '"':
                    closed = True
                    break
                k += 1
            if not closed:
                is_valid = False
                warn = "引号未闭合，已按普通搜索执行"
                clean_parts.append(raw[i:])
                i = n
                break
            ph = raw[j:k]
            if ph:
                if not phrase:
                    phrase = ph
                clean_parts.append(ph)
            i = k + 1
            continue

        # === 普通词/连续文本 ===
        k = i
        while k < n and not raw[k].isspace():
            k += 1
        tok = raw[i:k]
        # 布尔关键字（OR / AND / NOT）仅参与 AST 构建，不进入检索文本 clean_query
        if tok and tok.upper() not in ("OR", "AND", "NOT"):
            clean_parts.append(tok)
        i = k

    clean_query = " ".join(clean_parts).strip()
    if not is_valid:
        # 降级：引号未闭合时把原文作为普通搜索文本，不丢词
        clean_query = raw.strip()

    # === 增量（P1-1）：构建布尔 AST（与上面扁平字段同步）===
    expr, has_boolean = _build_bool_expr(raw)

    # Bug #3 修复（设计 §8 契约）：clean_query 仅拼接「非否定」 TermNode，
    # 否定 term（NOT ... / - 的布尔否定）仅作后过滤，不应污染检索向量。
    # 仅当 has_boolean 且存在非否定 TermNode 时，用这些词重拼 clean_query
    # （天然消解括号噪声）；否则保持原 clean_query 不变（向后兼容旧非布尔路径）。
    if is_valid and has_boolean:
        plain_terms = _collect_plain_terms(expr)
        if plain_terms:
            clean_query = " ".join(plain_terms).strip()

    return ParsedQuery(
        clean_query=clean_query,
        tag_filters=tag_filters,
        path_filters=path_filters,
        exclude_terms=exclude_terms,
        phrase=phrase,
        is_valid=is_valid,
        warn=warn,
        expr=expr,
        has_boolean=has_boolean,
    )


# --------------------------------------------------------------------------- #
# 布尔表达式构建（P1-1 新增，独立于扁平字段逻辑）
# --------------------------------------------------------------------------- #
def _tokenize_bool(raw: str):
    """扫描原始查询，产出布尔 token 序列，供递归下降解析构建 AST。

    返回 (tokens, has_boolean)。has_boolean 仅在出现 OR / NOT / 括号 /
    -tag: / -path: 时置 True（文本级 -排除词 不置，走旧 exclude_terms 路径）。

    token 形态：
      ('TAG', value, negated) / ('PATH', value, negated)
      ('TERM', text, negated)  # 普通词 / 短语 / 文本排除
      ('OR', None, False) / ('NOT', None, False)
      ('LPAREN', None) / ('RPAREN', None)
    """
    tokens: List[tuple] = []
    has_boolean = False
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c.isspace():
            i += 1
            continue

        # 括号 → 门控布尔
        if c == '(':
            has_boolean = True
            tokens.append(('LPAREN', None, False))
            i += 1
            continue
        if c == ')':
            has_boolean = True
            tokens.append(('RPAREN', None, False))
            i += 1
            continue

        # tag: / -tag:
        if raw.startswith("tag:", i) or raw.startswith("-tag:", i):
            negated = raw.startswith("-tag:", i)
            i += 5 if negated else 4
            if negated:
                has_boolean = True
            j = i
            while j < n and not raw[j].isspace() and raw[j] not in '()':
                j += 1
            val = raw[i:j]
            if val:
                tokens.append(('TAG', val, negated))
            i = j
            continue

        # path: / -path:
        if raw.startswith("path:", i) or raw.startswith("-path:", i):
            negated = raw.startswith("-path:", i)
            i += 6 if negated else 5
            if negated:
                has_boolean = True
            j = i
            while j < n and not raw[j].isspace() and raw[j] not in '()':
                j += 1
            val = raw[i:j]
            if val:
                tokens.append(('PATH', val, negated))
            i = j
            continue

        # - 文本排除：-词 或 -"短语"
        if c == '-':
            j = i + 1
            if j < n and raw[j] == '"':
                k = j + 1
                closed = False
                while k < n:
                    if raw[k] == '"':
                        closed = True
                        break
                    k += 1
                if closed:
                    term = raw[j + 1:k]
                    if term:
                        tokens.append(('TERM', term, True))
                    # 文本排除不置 has_boolean（走旧 exclude_terms）
                    i = k + 1
                    continue
                else:
                    # 未闭合引号：退化为整段普通词，不报错
                    tokens.append(('TERM', raw[i:], False))
                    i = n
                    continue
            else:
                k = j
                while k < n and not raw[k].isspace() and raw[k] not in '()':
                    k += 1
                term = raw[j:k]
                if term:
                    tokens.append(('TERM', term, True))
                i = k
                continue

        # "短语"
        if c == '"':
            j = i + 1
            k = j
            closed = False
            while k < n:
                if raw[k] == '"':
                    closed = True
                    break
                k += 1
            if closed:
                ph = raw[j:k]
                if ph:
                    tokens.append(('TERM', ph, False))
                i = k + 1
                continue
            else:
                # 未闭合：整段作为普通词（降级）
                tokens.append(('TERM', raw[i:], False))
                i = n
                continue

        # 读一个普通 token（可能是关键字 OR / AND / NOT 或普通词）
        k = i
        while k < n and not raw[k].isspace() and raw[k] not in '()':
            k += 1
        tok = raw[i:k]
        up = tok.upper()
        if up == 'OR':
            has_boolean = True
            tokens.append(('OR', None, False))
        elif up == 'AND':
            # 显式 AND 视为隐式连接（分隔符，不产生独立节点）
            pass
        elif up == 'NOT':
            has_boolean = True
            tokens.append(('NOT', None, False))
        else:
            tokens.append(('TERM', tok, False))
        i = k

    return tokens, has_boolean


def _parse_bool_tokens(tokens, has_boolean):
    """递归下降解析布尔 token 序列为 AST。

    优先级（由低到高）：OR > 隐式 AND（相邻项）> NOT（前缀）> 原子 / 括号。
    """
    if not tokens:
        return None, has_boolean
    pos = 0
    n = len(tokens)

    def peek():
        return tokens[pos] if pos < n else None

    def parse_or():
        nonlocal pos
        left = parse_and()
        while peek() is not None and peek()[0] == 'OR':
            pos += 1
            right = parse_and()
            if isinstance(left, OrNode):
                left.children.append(right)
            else:
                left = OrNode([left, right])
        return left

    def parse_and():
        nonlocal pos
        children = [parse_unary()]
        while True:
            t = peek()
            if t is None:
                break
            if t[0] in ('OR', 'RPAREN'):
                break
            # TAG / PATH / TERM / LPAREN / NOT 都开始一个新的 AND 操作数
            children.append(parse_unary())
        if len(children) == 1:
            return children[0]
        return AndNode(children)

    def parse_unary():
        nonlocal pos
        t = peek()
        if t is None:
            return None
        if t[0] == 'NOT':
            pos += 1
            return NotNode(parse_unary())
        return parse_primary()

    def parse_primary():
        nonlocal pos
        t = peek()
        if t is None:
            return None
        if t[0] == 'LPAREN':
            pos += 1
            inner = parse_or()
            if peek() is not None and peek()[0] == 'RPAREN':
                pos += 1
            return inner
        if t[0] == 'TAG':
            pos += 1
            return NotNode(TagNode(t[1])) if t[2] else TagNode(t[1])
        if t[0] == 'PATH':
            pos += 1
            return NotNode(PathNode(t[1])) if t[2] else PathNode(t[1])
        if t[0] == 'TERM':
            pos += 1
            return NotNode(TermNode(t[1])) if t[2] else TermNode(t[1])
        # 意外 token（如孤立 RPAREN）：跳过
        pos += 1
        return parse_primary()

    expr = parse_or()
    return expr, has_boolean


def _build_bool_expr(raw: str):
    """从原始查询构建布尔 AST（与扁平字段解析相互独立、互不干扰）。"""
    tokens, has_boolean = _tokenize_bool(raw)
    return _parse_bool_tokens(tokens, has_boolean)


def _collect_plain_terms(node: Optional[BoolExpr]) -> List[str]:
    """收集非否定 TermNode 文本，用于按设计 §8 重拼 clean_query。

    NotNode 子树的词一律不进入检索文本（否定词仅作后过滤）；TagNode/PathNode
    无检索文本；AndNode/OrNode 递归收集所有非否定 TermNode。返回顺序即 AST 中序。
    """
    if node is None:
        return []
    if isinstance(node, TermNode):
        return [node.text] if not node.negated else []
    if isinstance(node, NotNode):
        return []  # 整棵否定子树不贡献正向检索词
    if isinstance(node, (AndNode, OrNode)):
        out: List[str] = []
        for ch in node.children:
            out.extend(_collect_plain_terms(ch))
        return out
    # TagNode / PathNode → 无检索文本
    return []


# --------------------------------------------------------------------------- #
# 左栏多选筛选辅助（P1-1 新增）
# --------------------------------------------------------------------------- #
def build_tag_filter_parsed(tags: List[str], op: str = "AND") -> ParsedQuery:
    """由左栏多选标签 + AND/OR 构造 ParsedQuery。

    op ∈ {"AND","OR"}（大写约定，见设计 §8）。
    单标签 AND → expr=AndNode([TagNode(t)])，且 flat tag_filters=[t]（旧路径兼容）。
    空 tags → 返回 clean_query="" 的空 ParsedQuery。
    """
    tags = list(tags or [])
    if not tags:
        return ParsedQuery(clean_query="", expr=None, has_boolean=False)
    op = (op or "AND").upper()
    nodes = [TagNode(t) for t in tags]
    if op == "OR":
        expr: Optional[BoolExpr] = OrNode(nodes)
        has_boolean = True
    else:
        # AND：单标签亦用 AndNode 包装（与旧交集路径同构）；has_boolean=False 走旧路径
        expr = AndNode(nodes) if len(nodes) > 1 else nodes[0]
        has_boolean = False
    return ParsedQuery(
        clean_query="",
        tag_filters=list(tags),
        path_filters=[],
        exclude_terms=[],
        phrase="",
        is_valid=True,
        warn="",
        expr=expr,
        has_boolean=has_boolean,
    )


def combine_parsed(
    text_parsed: ParsedQuery,
    tag_parsed: Optional[ParsedQuery],
) -> ParsedQuery:
    """合并文本框解析结果与左栏标签筛选（AND 语义）。

    tag_parsed 为 None → 直接返回 text_parsed（支持空框仅左栏筛选时浏览）。
    两者皆「简单」（has_boolean 均为 False）→ 合并 flat 字段、has_boolean 保持 False（走旧交集路径）。
    任一含布尔 → 外层 AndNode 包裹、has_boolean=True（走新路径）。
    """
    if tag_parsed is None:
        return text_parsed

    text_parsed = text_parsed or ParsedQuery(clean_query="")
    tag_parsed = tag_parsed or ParsedQuery(clean_query="")

    if not text_parsed.has_boolean and not tag_parsed.has_boolean:
        return ParsedQuery(
            clean_query=text_parsed.clean_query,
            tag_filters=list(text_parsed.tag_filters) + list(tag_parsed.tag_filters),
            path_filters=list(text_parsed.path_filters) + list(tag_parsed.path_filters),
            exclude_terms=list(text_parsed.exclude_terms) + list(tag_parsed.exclude_terms),
            phrase=text_parsed.phrase,
            is_valid=text_parsed.is_valid and tag_parsed.is_valid,
            warn=text_parsed.warn or tag_parsed.warn,
            expr=None,
            has_boolean=False,
        )

    # 任一含布尔 → 外层 AndNode 包裹
    nodes: List[BoolExpr] = []
    if text_parsed.expr is not None:
        nodes.append(text_parsed.expr)
    if tag_parsed.expr is not None:
        nodes.append(tag_parsed.expr)
    combined_expr = AndNode(nodes) if len(nodes) > 1 else (nodes[0] if nodes else None)
    return ParsedQuery(
        clean_query=text_parsed.clean_query,
        tag_filters=list(text_parsed.tag_filters) + list(tag_parsed.tag_filters),
        path_filters=list(text_parsed.path_filters) + list(tag_parsed.path_filters),
        exclude_terms=list(text_parsed.exclude_terms) + list(tag_parsed.exclude_terms),
        phrase=text_parsed.phrase,
        is_valid=text_parsed.is_valid and tag_parsed.is_valid,
        warn=text_parsed.warn or tag_parsed.warn,
        expr=combined_expr,
        has_boolean=True,
    )
