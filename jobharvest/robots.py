"""Robots rules with query strings, wildcard matching and longest-rule precedence."""
import re
from urllib.parse import urlsplit


class RobotsPolicy:
    def __init__(self, text):
        self.groups = []
        agents, rules, has_directives = [], [], False
        for raw in text.splitlines() + ["User-agent: __end__"]:
            line = raw.split("#", 1)[0].strip()
            key, separator, value = line.partition(":")
            if not separator:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if has_directives:
                    self.groups.append((agents, rules))
                    agents, rules, has_directives = [], [], False
                agents.append(value.lower())
            elif key in {"allow", "disallow"} and agents:
                has_directives = True
                if value:
                    rules.append((value, key == "allow"))

    def can_fetch(self, agent, url):
        agent = agent.split("/", 1)[0].lower()
        matches = []
        for agents, rules in self.groups:
            specificity = max((0 if a == "*" else len(a) for a in agents if a == "*" or a in agent), default=-1)
            if specificity >= 0:
                matches.append((specificity, rules))
        if not matches:
            return True
        best = max(s for s, _ in matches)
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        applicable = []
        for specificity, rules in matches:
            if specificity != best:
                continue
            for pattern, allow in rules:
                anchored = pattern.endswith("$")
                literal = pattern[:-1] if anchored else pattern
                expression = "^" + ".*".join(re.escape(part) for part in literal.split("*")) + ("$" if anchored else "")
                if re.search(expression, target):
                    applicable.append((len(literal.replace("*", "")), allow))
        return max(applicable, default=(0, True))[1]
