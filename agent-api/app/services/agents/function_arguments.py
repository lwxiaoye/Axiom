"""Validate arguments before business code; never resolve remote schema references."""

import json

from jsonschema.validators import validator_for
from referencing import Registry


def compile_validators(tools):
    validators = {}
    for name, tool in tools.items():
        schema = tool.parameters
        validator = validator_for(schema)
        validator.check_schema(schema)
        validators[name] = validator(schema, registry=Registry())
    return validators


def _invalid_constant(value):
    raise ValueError(f"参数中不允许非 JSON 数值 {value}")


def _unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"参数存在重复字段 {key}")
        value[key] = item
    return value


def parse_arguments(raw, validator):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("工具参数必须是完整的 JSON 对象字符串")
    try:
        args = json.loads(raw, parse_constant=_invalid_constant, object_pairs_hook=_unique_keys)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"工具参数 JSON 无效，请重新生成完整对象：{str(exc)[:300]}") from exc
    if not isinstance(args, dict):
        raise ValueError("工具参数必须是 JSON 对象，不能是数组、null 或字符串")
    error = next(validator.iter_errors(args), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ValueError(f"工具参数不符合 Schema（{path}）：{error.message[:500]}")
    return args
