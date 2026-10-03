"""Generate bounded PowerDNS Lua records from data, never arbitrary Lua input."""
import ipaddress
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["country", "continent", "netmask"]
    matches: list[str] = Field(min_length=1, max_length=64)
    address: str

    @field_validator("address")
    @classmethod
    def address_value(cls, value):
        return str(ipaddress.IPv4Address(value))

    @model_validator(mode="after")
    def selectors(self):
        if self.kind == "netmask":
            self.matches = [str(ipaddress.ip_network(v, strict=False)) for v in self.matches]
        else:
            self.matches = [v.upper() for v in self.matches]
            if any(not re.fullmatch(r"[A-Z]{2}", v) for v in self.matches):
                raise ValueError("Use two-letter codes")
            if self.kind == "continent" and set(self.matches) - {"AF", "AN", "AS", "EU", "NA", "OC", "SA"}:
                raise ValueError("Invalid continent code")
        return self


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[Rule] = Field(default_factory=list, max_length=32)
    fallback: str
    @field_validator("fallback")
    @classmethod
    def fallback_address(cls, value):
        return str(ipaddress.IPv4Address(value))


def render(policy: Policy):
    clauses = []
    for index, rule in enumerate(policy.rules):
        match = "{" + ",".join("'" + value + "'" for value in rule.matches) + "}"
        clauses.append(f"{'if' if index == 0 else 'elseif'} {rule.kind}({match}) then return '{rule.address}'")
    script = ";" + (" ".join(clauses) + f" else return '{policy.fallback}' end" if clauses else f"return '{policy.fallback}'")
    # DNS TXT-style chunks must each fit in one character-string for AXFR.
    chunks = [script[i:i + 240] for i in range(0, len(script), 240)]
    return {"type": "LUA", "answer_type": "A", "script": script,
            "content": "A " + " ".join(json.dumps(chunk) for chunk in chunks),
            "precedence": "First matching rule wins; fallback handles unknown locations.",
            "warning": "Requires PowerDNS Lua records enabled and GeoIP/MaxMind configured. Location uses ECS when supplied, otherwise the recursive resolver address. This is DNS steering, not an access-control firewall."}
