"""Golden examples agree with the models, the YAML, the JSON Schemas and the mocks."""

import json
from collections import defaultdict
from collections.abc import Iterator
from functools import cache
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from app.contracts.artifacts import (
    MANIFEST_PATH,
    OPENAPI_PATH,
    SCHEMAS_DIR,
    STANDALONE_SCHEMAS,
)
from app.contracts.common import DEFAULT_ERROR_MESSAGES, ErrorCode
from app.contracts.examples import EXAMPLES, EXAMPLES_DIR, Example

# Demo scenarios of the master plan (§G): all of them have golden payloads, and
# S1-S16 have a complete ConversationReply (P01 prompt, item 5).
ALL_SCENARIOS = {f"S{n}" for n in range(1, 19)}
REPLY_SCENARIOS = {f"S{n}" for n in range(1, 17)}
REPLY_CARRIERS = {"ChatMessageResponse", "WhatsAppSimulationResult", "PbxTurnResponse"}


@cache
def published() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    return document


@cache
def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data


def _ids(example: Example) -> str:
    return example.name


MODELLED = [example for example in EXAMPLES if example.model]


def test_every_example_file_is_registered_once() -> None:
    names = [example.name for example in EXAMPLES]

    assert len(names) == len(set(names))
    assert {path.stem for path in EXAMPLES_DIR.glob("*.json")} == set(names)


@pytest.mark.parametrize("example", MODELLED, ids=_ids)
def test_example_round_trips_through_its_model(example: Example) -> None:
    assert example.model is not None
    data = example.load()

    parsed = example.model.model_validate(data)

    # Requests may omit defaulted fields; everything else serialises back verbatim.
    assert parsed.model_dump(mode="json", exclude_unset=example.is_request) == data


@pytest.mark.parametrize("example", MODELLED, ids=_ids)
def test_example_validates_against_the_published_yaml(example: Example) -> None:
    assert example.model is not None
    validator = Draft202012Validator(
        {"$ref": f"#/components/schemas/{example.model.__name__}", **published()}
    )

    errors = [f"{e.json_path}: {e.message}" for e in validator.iter_errors(example.load())]

    assert not errors


def _instances(value: object) -> Iterator[BaseModel]:
    """Every model instance inside a parsed example, nested ones included."""
    if isinstance(value, BaseModel):
        yield value
        for field in value.__dict__.values():
            yield from _instances(field)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _instances(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _instances(item)


@cache
def instances_by_model() -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = defaultdict(list)
    for example in MODELLED:
        assert example.model is not None
        for instance in _instances(example.model.model_validate(example.load())):
            found[type(instance).__name__].append(instance.model_dump(mode="json"))
    return found


@pytest.mark.parametrize("name", [name for name, _, _ in STANDALONE_SCHEMAS])
def test_standalone_schemas_accept_every_golden_instance(name: str) -> None:
    schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    concrete = {"WsEvent": ("WsHandoffStatusEvent", "WsMessageEvent")}.get(name, (name,))
    payloads = [payload for model in concrete for payload in instances_by_model()[model]]

    assert payloads, f"no golden instance exercises {name}"  # never a vacuous pass
    for payload in payloads:
        assert not [e.message for e in validator.iter_errors(payload)], name


def test_every_operation_has_a_mock_response() -> None:
    operations = manifest()["operations"]

    assert {op["operationId"] for op in operations} == {
        operation["operationId"]
        for item in published()["paths"].values()
        for operation in item.values()
    }
    for op in operations:
        assert op["responses"], op["operationId"]
        assert {r["status"] for r in op["responses"]} & {200, 201}, op["operationId"]


def test_mock_examples_exist_and_operations_with_a_body_have_a_request() -> None:
    for op in manifest()["operations"]:
        for entry in op["requests"] + op["responses"]:
            assert (MANIFEST_PATH.parent.parent / entry["example"]).is_file()
        operation = published()["paths"][op["path"]][op["method"].lower()]
        body = operation.get("requestBody", {})
        if body.get("required") and "application/json" in body.get("content", {}):
            assert op["requests"], op["operationId"]


def test_chat_mocks_can_be_told_apart() -> None:
    chat = next(op for op in manifest()["operations"] if op["operationId"] == "postChatMessage")
    needles = [r["match"]["text_contains"] for r in chat["responses"]]
    assert len(needles) == len(chat["requests"])

    assert len(needles) == len(set(needles))
    for request in chat["requests"]:
        text = json.loads((MANIFEST_PATH.parent.parent / request["example"]).read_text())["text"]
        hits = [needle for needle in needles if needle.lower() in text.lower()]
        assert len(hits) == 1, (request["example"], hits)


def test_demo_scenarios_are_covered() -> None:
    covered = set().union(*(example.scenarios for example in EXAMPLES))
    with_reply = set().union(
        *(
            example.scenarios
            for example in EXAMPLES
            if example.model and example.model.__name__ in REPLY_CARRIERS
        )
    )

    assert covered == ALL_SCENARIOS
    assert with_reply >= REPLY_SCENARIOS


def test_there_is_one_example_per_error_code() -> None:
    codes = {example.error_code for example in EXAMPLES if example.error_code}
    messages = {
        example.error_code: example.load()["error"]["message"]
        for example in EXAMPLES
        if example.error_code
    }

    assert codes == set(ErrorCode)
    assert messages == dict(DEFAULT_ERROR_MESSAGES)


def test_mock_errors_point_to_the_example_of_their_code() -> None:
    for op in manifest()["operations"]:
        assert any(error["code"] == "ERROR_INTERNO" for error in op["errors"]), op["operationId"]
        for error in op["errors"]:
            data = json.loads((MANIFEST_PATH.parent.parent / error["example"]).read_text())
            assert data["error"]["code"] == error["code"]


def test_values_only_the_real_service_knows_are_flagged() -> None:
    flagged = {
        entry["example"]
        for op in manifest()["operations"]
        for entry in op["requests"] + op["responses"]
        if entry["pendiente_de_servicio_real"]
    }

    assert "examples/metrics.channels.json" in flagged
    assert "examples/chat.mercancia-peligrosa.response.json" in flagged
    assert "examples/cotizar.response.json" not in flagged


def test_examples_reproduce_the_demo_numbers() -> None:
    by_name = {example.name: example.load() for example in EXAMPLES}
    plain = by_name["cotizar.response"]["calculo"]
    cold = by_name["cotizar.cadena-frio.response"]["calculo"]
    floor = by_name["cotizar.flete-minimo.response"]["calculo"]

    # S7
    assert (plain["peso_volumetrico_kg"], plain["peso_facturable_kg"]) == (30.0, 45.5)
    assert (plain["seguro_cop"], plain["flete_base_cop"], plain["flete_total_cop"]) == (
        125000,
        65975,
        190975,
    )
    assert (cold["recargos"][0]["valor_cop"], cold["flete_total_cop"]) == (16494, 207469)
    # S9
    assert floor["peso_volumetrico_kg"] == 6.0
    assert (floor["seguro_cop"], floor["flete_base_cop"], floor["flete_total_cop"]) == (
        4500,
        18000,
        22500,
    )
    assert floor["flete_minimo_aplicado"] is True
    # S1 and S5
    assert by_name["tracking.en-transito"]["ultima_temperatura_c"] == 3.8
    assert by_name["tracking.entregado"]["prueba_entrega"]["recibido_por_iniciales"] == "R. B."
    # S14 escalates to Jurídico/PQRS (DEC-E2)
    pqrs = by_name["pqrs.siniestro.response"]
    assert pqrs["escalamiento"]["primary"]["id_criterio"] == "CRIT-003"
    # S4 escalates to Operaciones with URGENTE priority
    handoff = by_name["handoff.operaciones"]
    assert (handoff["cola_destino"], handoff["prioridad"]) == ("OPERACIONES", "URGENTE")


def test_same_business_result_on_every_channel() -> None:
    by_name = {example.name: example.load() for example in EXAMPLES}
    web = by_name["chat.tracking.response"]["reply"]
    whatsapp = by_name["whatsapp.simulate.response"]["results"][0]["reply"]
    pbx = by_name["pbx.turn.response"]["reply"]

    assert web["meta"]["evidence_ids"] == whatsapp["meta"]["evidence_ids"]
    assert web["meta"]["evidence_ids"] == pbx["meta"]["evidence_ids"]
    assert (whatsapp["channel"], whatsapp["meta"]["delivery_mode"]) == ("WHATSAPP", "SIMULATED")
    assert (pbx["channel"], pbx["meta"]["delivery_mode"]) == ("PBX", "SIMULATED")
