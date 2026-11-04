"""RAG corpus (P05): load, normalise and chunk the operational sources.

* Markdown manuals are split by their own heading tree; a chunk never crosses
  a top-level section. The 512-token ceiling (64 overlap) only applies when a
  section is longer (DEC-R2: recommendation of the challenge, not a dogma).
* JSON/CSV records become one Spanish sentence each, with metadata.
* The challenge text and the escalation rules JSON are specification, not
  operational knowledge: they are not indexed.
"""

import csv
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.contracts.rag import ChunkMetadata, ChunkTipo, DocCode

MAX_TOKENS = 512
OVERLAP_TOKENS = 64
_TOKEN = re.compile(r"\w+|[^\w\s]")


def count_tokens(text: str) -> int:
    """Approximation (words and punctuation): the embedding model's tokenizer is not exposed."""
    return len(_TOKEN.findall(text))


@dataclass(frozen=True)
class ChunkContext:
    ingested_at: datetime
    zones: dict[str, str]  # departamento → zona (DEC-R2 proposal)


@dataclass(frozen=True)
class SourceDocument:
    doc_code: DocCode
    doc_title: str
    source_file: str
    content_hash: str
    loader: Callable[["SourceDocument", str, ChunkContext], list["Chunk"]]
    path: Path


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str  # what the user sees in a citation
    embed_text: str  # with the context prefix "[DOC › section]"
    metadata: ChunkMetadata


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _clean_heading(raw: str) -> str:
    return re.sub(r"\*\*|`", "", raw).strip().rstrip(":")


def _split_long(text: str) -> list[str]:
    """Split on paragraph/list boundaries with ~64 tokens of overlap, only if needed."""
    if count_tokens(text) <= MAX_TOKENS:
        return [text]
    blocks = [b for b in re.split(r"\n(?=\s*(?:\*|\d+\.|-|\n))", text) if b.strip()]
    pieces: list[str] = []
    current: list[str] = []
    for block in blocks:
        if current and count_tokens("\n".join([*current, block])) > MAX_TOKENS:
            pieces.append("\n".join(current))
            tail: list[str] = []
            while current and count_tokens("\n".join([current[-1], *tail])) <= OVERLAP_TOKENS:
                tail.insert(0, current.pop())
            current = tail
        current.append(block)
    if current:
        pieces.append("\n".join(current))
    return pieces


def _aliases(text: str) -> list[str]:
    examples = re.search(r"\((?:Ej\.|ej\.)\s*([^)]*)\)", text)
    if not examples:
        return []
    return [a.strip(" .") for a in re.split(r",|/", examples.group(1)) if a.strip(" .")]


def _manual_tipo(doc: DocCode, top_section: str) -> ChunkTipo:
    if doc is DocCode.DOC_PQRS_2026_04:
        return ChunkTipo.REGLAMENTO_PQRS
    if "EMBALAJE" in top_section.upper():
        return ChunkTipo.EMBALAJE
    if "GUÍA DE ATENCIÓN" in top_section.upper():
        return ChunkTipo.POLITICA
    return ChunkTipo.NORMATIVA_CARGA


def load_markdown(doc: SourceDocument, raw: str, ctx: ChunkContext) -> list[Chunk]:
    sections: list[tuple[list[str], list[str]]] = []  # (heading path, lines)
    path: list[str] = []
    lines: list[str] = []
    in_fence = False
    for line in raw.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else re.match(r"^(#{2,4})\s+(.*)$", line)
        if heading:
            if lines:
                sections.append((path.copy(), lines))
            depth = len(heading.group(1)) - 2  # "##" is the top section
            path = [*path[:depth], _clean_heading(heading.group(2))]
            lines = []
        elif path:  # text before the first "##" is the document header: not knowledge
            lines.append(line)
    if lines:
        sections.append((path.copy(), lines))

    chunks: list[Chunk] = []
    for heading_path, body in sections:
        text = "\n".join(body).strip().strip("-").strip()
        if not text:
            continue
        section_path = " > ".join(heading_path)
        clase = re.match(r"Clase (\d)", heading_path[-1])
        for piece in _split_long(text):
            index = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_code.value}:{index:04d}",
                    text=piece,
                    embed_text=f"[{doc.doc_code.value} › {heading_path[-1]}] {piece}",
                    metadata=ChunkMetadata(
                        doc_code=doc.doc_code,
                        doc_title=doc.doc_title,
                        source_file=doc.source_file,
                        section_path=section_path,
                        tipo=_manual_tipo(doc.doc_code, heading_path[0]),
                        zona=None,
                        clase_un=clase.group(1) if clase else None,
                        aliases=_aliases(piece),
                        chunk_index=index,
                        content_hash=_sha(piece),
                        ingested_at=ctx.ingested_at,
                    ),
                )
            )
    return chunks


def _yes(value: object) -> str:
    return "sí" if value else "no"


def _record_chunk(
    doc: SourceDocument,
    ctx: ChunkContext,
    index: int,
    section: str,
    text: str,
    tipo: ChunkTipo,
    departamento: str,
) -> Chunk:
    return Chunk(
        chunk_id=f"{doc.doc_code.value}:{index:04d}",
        text=text,
        embed_text=f"[{doc.doc_code.value} › {section}] {text}",
        metadata=ChunkMetadata(
            doc_code=doc.doc_code,
            doc_title=doc.doc_title,
            source_file=doc.source_file,
            section_path=f"{doc.doc_title} > {section}",
            tipo=tipo,
            zona=ctx.zones.get(departamento),
            clase_un=None,
            aliases=[],
            chunk_index=index,
            content_hash=_sha(text),
            ingested_at=ctx.ingested_at,
        ),
    )


def load_coverage(doc: SourceDocument, raw: str, ctx: ChunkContext) -> list[Chunk]:
    chunks = []
    for index, r in enumerate(json.loads(raw)):
        text = (
            f"Cobertura en {r['municipio']} ({r['departamento']}): "
            f"tipo {r['tipo_cobertura']}; tiempo de "
            f"entrega estándar {r['tiempo_entrega_estandar_horas']} horas; servicio same day "
            f"{_yes(r['servicio_same_day_disponible'])}; servicio refrigerado "
            f"{_yes(r['servicio_refrigerado_disponible'])}; peso máximo por paquete "
            f"{r['peso_maximo_paquete_kg']} kg; volumen máximo {r['volumen_maximo_m3']} m³; "
            "hub asignado "
            f"{r['hub_operativo_asignado']}; frecuencia de despacho {r['frecuencia_despacho']}; "
            f"restricciones: {r['restricciones_operativas']}"
        )
        chunks.append(
            _record_chunk(
                doc, ctx, index, r["municipio"], text, ChunkTipo.COBERTURA, r["departamento"]
            )
        )
    return chunks


def load_tariffs(doc: SourceDocument, raw: str, ctx: ChunkContext) -> list[Chunk]:
    chunks = []
    for index, r in enumerate(csv.DictReader(raw.lstrip("﻿").splitlines())):
        text = (
            f"Tarifa de la ruta {r['codigo_trayecto']} "
            f"{r['ciudad_origen']} → {r['ciudad_destino']} "
            f"({r['tipo_cobertura']}): {r['tarifa_base_kg_real_cop']} COP por kg real y "
            f"{r['tarifa_base_kg_volumetrico_cop']} COP por kg volumétrico; flete mínimo "
            f"{r['flete_minimo_cop']} COP; seguro {r['porcentaje_seguro']} % con mínimo de matriz "
            f"{r['seguro_minimo_cop']} COP; "
            f"recargo cadena de frío {r['recargo_cadena_frio_pct']} %; "
            f"recargo mercancía peligrosa {r['recargo_mercancia_peligrosa_cop']} COP; "
            "tiempo estimado "
            f"{r['tiempo_entrega_estimado_horas']} horas. Observaciones: {r['observaciones_ruta']}"
        )
        section = f"{r['ciudad_origen']} → {r['ciudad_destino']}"
        chunks.append(
            _record_chunk(
                doc, ctx, index, section, text, ChunkTipo.TARIFA, r["departamento_destino"]
            )
        )
    return chunks


def load_zones(zones_file: Path | None) -> dict[str, str]:
    if zones_file is None or not zones_file.exists():
        return {}
    zones: dict[str, str] = json.loads(zones_file.read_text(encoding="utf-8"))["zonas"]
    return zones


def discover(source_dir: Path) -> list[SourceDocument]:
    specs = (
        (
            DocCode.DOC_OPS_2026_03,
            "Manual operativo de mercancías peligrosas, estándares de embalaje y "
            "restricciones de carga",
            "manual_de_mercanc_as_peligrosas_y_embalaje.md",
            load_markdown,
        ),
        (
            DocCode.DOC_PQRS_2026_04,
            "Protocolo general de peticiones, quejas, reclamos y procedimiento de indemnizaciones",
            "protocolo_de_pqrs_y_procedimiento_de_indemnizaciones.md",
            load_markdown,
        ),
        (
            DocCode.COBERTURA,
            "Directorio de cobertura y tiempos de entrega",
            "directorio_cobertura_y_tiempos.json",
            load_coverage,
        ),
        (
            DocCode.TARIFAS,
            "Matriz de tarifas y fletes",
            "matriz_tarifas_y_fletes.csv",
            load_tariffs,
        ),
    )
    documents = []
    for code, title, name, loader in specs:
        path = source_dir / name
        documents.append(
            SourceDocument(
                doc_code=code,
                doc_title=title,
                source_file=name,
                content_hash=_sha(path.read_text(encoding="utf-8")),
                loader=loader,
                path=path,
            )
        )
    return documents


def chunk_document(doc: SourceDocument, ctx: ChunkContext) -> list[Chunk]:
    return doc.loader(doc, doc.path.read_text(encoding="utf-8"), ctx)
