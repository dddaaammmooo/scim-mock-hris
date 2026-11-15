"""Group query language and membership resolver.

Grammar (case-insensitive keywords):
    expr     := and_expr ("or" and_expr)*
    and_expr := not_expr ("and" not_expr)*
    not_expr := "not" not_expr | primary
    primary  := "(" expr ")" | memberOf("group") | field "exists" | field op value
    op       := == != < <= > >= in contains startsWith endsWith matches

Fields are dotted paths into the user record or one of the ALIASES. Nested groups
(memberOf) are flattened; cycles, unknown fields and unknown groups are errors.
"""
import re

# Friendly names for commonly queried fields. Any dotted path into the user JSON
# (e.g. work.custom.costCentre) also works.
ALIASES = {
    "division": "work.custom.division",
    "department": "work.department",
    "title": "work.title",
    "team": "work.custom.team",
    "site": "work.site",
    "startDate": "work.startDate",
    "isManager": "work.isManager",
    "manager": "work.reportsTo.email",
    "directReports": "work.directReports",
    "costCentre": "work.custom.costCentre",
    "jobLevel": "work.custom.jobLevel",
    "legalEntity": "work.custom.legalEntity",
    "region": "work.custom.region",
    "employeeId": "work.employeeIdInCompany",
    "status": "internal.status",
    "lifecycle": "internal.lifecycleStatus",
    "employmentType": "payroll.employment.type",
    "country": "address.country",
    "state": "address.state",
}


class QueryError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<number>-?\d+(?:\.\d+)?(?![\w.]))
  | (?P<op>==|!=|<=|>=|<|>)
  | (?P<punct>[()\[\],])
  | (?P<ident>[A-Za-z_][\w.]*)
""", re.VERBOSE)

KEYWORDS = {"and", "or", "not", "in", "contains", "startswith", "endswith",
            "matches", "exists", "true", "false", "null", "memberof"}
WORD_OPS = {"in", "contains", "startswith", "endswith", "matches"}


def tokenize(text):
    tokens, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise QueryError(f"unexpected character {text[pos]!r} at position {pos}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group()
        if kind == "ws":
            continue
        if kind == "string":
            val = re.sub(r"\\(.)", r"\1", val[1:-1])
        elif kind == "number":
            val = float(val) if "." in val else int(val)
        elif kind == "ident" and val.lower() in KEYWORDS:
            kind, val = "kw", val.lower()
        tokens.append((kind, val))
    tokens.append(("eof", None))
    return tokens


# --------------------------------------------------------------------------- #
# Parser  ->  AST of tuples
#   expr    := and_expr ("or" and_expr)*
#   and_expr:= not_expr ("and" not_expr)*
#   not_expr:= "not" not_expr | primary
#   primary := "(" expr ")" | memberOf "(" string ")" | field "exists"
#            | field op value
# --------------------------------------------------------------------------- #
class Parser:
    def __init__(self, text):
        self.text = text
        self.tokens = tokenize(text)
        self.i = 0

    def peek(self):
        return self.tokens[self.i]

    def next(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, kind, val=None):
        tok = self.next()
        if tok[0] != kind or (val is not None and tok[1] != val):
            want = val or kind
            raise QueryError(f"expected {want!r} but found {tok[1]!r}")
        return tok

    def parse(self):
        node = self.or_expr()
        if self.peek()[0] != "eof":
            raise QueryError(f"unexpected {self.peek()[1]!r} after end of expression")
        return node

    def or_expr(self):
        node = self.and_expr()
        while self.peek() == ("kw", "or"):
            self.next()
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.peek() == ("kw", "and"):
            self.next()
            node = ("and", node, self.not_expr())
        return node

    def not_expr(self):
        if self.peek() == ("kw", "not"):
            self.next()
            return ("not", self.not_expr())
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok == ("punct", "("):
            self.next()
            node = self.or_expr()
            self.expect("punct", ")")
            return node
        if tok == ("kw", "memberof"):
            self.next()
            self.expect("punct", "(")
            name = self.expect("string")[1]
            self.expect("punct", ")")
            return ("memberOf", name)
        if tok[0] != "ident":
            raise QueryError(f"expected a field name, memberOf(...) or '(' but found {tok[1]!r}")
        field = ALIASES.get(self.next()[1], tok[1])
        op_tok = self.next()
        if op_tok == ("kw", "exists"):
            return ("exists", field)
        if op_tok[0] == "op" or (op_tok[0] == "kw" and op_tok[1] in WORD_OPS):
            op = op_tok[1]
        else:
            raise QueryError(f"expected an operator after {tok[1]!r} but found {op_tok[1]!r}")
        value = self.list_value() if op == "in" else self.value()
        if op == "matches":
            if not isinstance(value, str):
                raise QueryError("matches requires a string regex")
            try:
                value = re.compile(value, re.IGNORECASE)
            except re.error as e:
                raise QueryError(f"invalid regex {value!r}: {e}")
        return ("cmp", field, op, value)

    def value(self):
        kind, val = self.next()
        if kind in ("string", "number"):
            return val
        if kind == "kw" and val in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[val]
        raise QueryError(f"expected a value but found {val!r}")

    def list_value(self):
        self.expect("punct", "[")
        items = []
        if self.peek() != ("punct", "]"):
            items.append(self.value())
            while self.peek() == ("punct", ","):
                self.next()
                items.append(self.value())
        self.expect("punct", "]")
        return items


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def get_path(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def norm(v):
    return v.casefold() if isinstance(v, str) else v


def compare_scalar(lhs, op, rhs):
    a, b = norm(lhs), norm(rhs) if not isinstance(rhs, (list, re.Pattern)) else rhs
    if op == "==":
        return a == b
    if op in ("<", ">", "<=", ">="):
        if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
            return False
        if isinstance(a, str) != isinstance(b, str):
            return False
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
    if op == "in":
        return a in [norm(x) for x in b]
    if not isinstance(a, str):
        return False
    if op == "contains":
        return str(b).casefold() in a
    if op == "startswith":
        return a.startswith(str(b).casefold())
    if op == "endswith":
        return a.endswith(str(b).casefold())
    if op == "matches":
        return bool(b.search(lhs))
    raise QueryError(f"unknown operator {op}")


def compare(lhs, op, rhs):
    if op == "!=":
        return not compare(lhs, "==", rhs)
    if isinstance(lhs, list):
        # list fields (e.g. nationality, directReports): true if ANY element matches
        if op == "contains":
            return any(norm(x) == norm(rhs) for x in lhs)
        return any(compare_scalar(x, op, rhs) for x in lhs)
    return compare_scalar(lhs, op, rhs)


class Resolver:
    def __init__(self, users, groups):
        self.users = users            # email -> user dict
        self.groups = groups          # name  -> group dict
        self.cache = {}
        self.known_paths = set()
        for u in users.values():
            self._collect_paths(u, "")
        self._asts = {}

    def _collect_paths(self, obj, prefix):
        for k, v in obj.items():
            p = f"{prefix}{k}"
            self.known_paths.add(p)
            if isinstance(v, dict):
                self._collect_paths(v, p + ".")

    def compile(self, query, where):
        try:
            ast = Parser(query).parse()
        except QueryError as e:
            raise QueryError(f"{where}: {e}\n    query: {query}")
        self._validate(ast, where)
        return ast

    def _validate(self, node, where):
        kind = node[0]
        if kind in ("and", "or"):
            self._validate(node[1], where)
            self._validate(node[2], where)
        elif kind == "not":
            self._validate(node[1], where)
        elif kind == "memberOf":
            if node[1] not in self.groups:
                raise QueryError(f"{where}: memberOf references unknown group {node[1]!r}")
        else:
            field = node[1]
            if field not in self.known_paths:
                raise QueryError(f"{where}: unknown field {field!r} (not present on any user; "
                                 f"aliases: {', '.join(sorted(ALIASES))})")

    def evaluate(self, node, user, stack):
        kind = node[0]
        if kind == "and":
            return self.evaluate(node[1], user, stack) and self.evaluate(node[2], user, stack)
        if kind == "or":
            return self.evaluate(node[1], user, stack) or self.evaluate(node[2], user, stack)
        if kind == "not":
            return not self.evaluate(node[1], user, stack)
        if kind == "memberOf":
            return user["email"] in self.resolve(node[1], stack)
        if kind == "exists":
            v = get_path(user, node[1])
            return v not in (None, "", [], {})
        _, field, op, value = node
        return compare(get_path(user, field), op, value)

    def run_query(self, ast, stack=()):
        return {e for e, u in self.users.items() if self.evaluate(ast, u, stack)}

    def resolve(self, name, stack=()):
        if name in self.cache:
            return self.cache[name]
        if name in stack:
            cycle = " -> ".join(list(stack[stack.index(name):]) + [name])
            raise QueryError(f"circular group reference: {cycle}")
        group = self.groups[name]
        stack = stack + (name,)
        members = set()
        for m in group.get("members", []):
            if m not in self.users:
                raise QueryError(f"group {name!r}: static member {m!r} has no file in users/")
            members.add(m)
        if group.get("query"):
            if name not in self._asts:
                self._asts[name] = self.compile(group["query"], f"group {name!r}")
            members |= self.run_query(self._asts[name], stack)
        self.cache[name] = frozenset(members)
        return self.cache[name]


def referenced_groups(ast):
    """Return the set of group names referenced via memberOf() in a parsed query."""
    kind = ast[0]
    if kind in ("and", "or"):
        return referenced_groups(ast[1]) | referenced_groups(ast[2])
    if kind == "not":
        return referenced_groups(ast[1])
    if kind == "memberOf":
        return {ast[1]}
    return set()


def parse(query):
    return Parser(query).parse()
