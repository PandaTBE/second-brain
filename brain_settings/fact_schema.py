"""Additive, cross-process-safe fact schema. Called under the existing schema lock."""

from datetime import UTC, datetime

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fact_entities(
 id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_snapshots(
 record_id TEXT NOT NULL,revision INTEGER NOT NULL,
 source_id TEXT NOT NULL,source_kind TEXT NOT NULL,source_object_id TEXT NOT NULL,
 source_folder_id TEXT NOT NULL,title TEXT NOT NULL,source_uri TEXT NOT NULL,text TEXT NOT NULL,
 project TEXT,message_at TEXT,source_created_at TEXT,source_modified_at TEXT,imported_at TEXT,
 PRIMARY KEY(record_id,revision)
);
CREATE TABLE IF NOT EXISTS fact_evidence(
 id TEXT PRIMARY KEY,fact_id TEXT NOT NULL,record_id TEXT NOT NULL,revision INTEGER NOT NULL,
 start INTEGER NOT NULL,end INTEGER NOT NULL,quote TEXT NOT NULL,status TEXT NOT NULL,
 observed_at TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1))
);
CREATE INDEX IF NOT EXISTS fact_evidence_entity ON fact_evidence(fact_id,active,observed_at,id);
CREATE INDEX IF NOT EXISTS fact_evidence_record ON fact_evidence(record_id,revision,active);
CREATE TABLE IF NOT EXISTS fact_relations(
 id TEXT PRIMARY KEY,from_fact TEXT NOT NULL,target_fact TEXT NOT NULL,
 record_id TEXT NOT NULL,revision INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
 quote TEXT NOT NULL,kind TEXT NOT NULL,observed_at TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1))
);
CREATE INDEX IF NOT EXISTS fact_relations_from ON fact_relations(from_fact,active);
CREATE TABLE IF NOT EXISTS fact_projection(
 record_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,card_hash TEXT NOT NULL,outcome TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS fact_card_changed AFTER UPDATE OF card_json,revision ON documents
WHEN OLD.card_json IS NOT NEW.card_json OR OLD.revision<>NEW.revision BEGIN
 DELETE FROM fact_projection WHERE record_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS fact_delete_document AFTER DELETE ON documents BEGIN
 DELETE FROM fact_evidence WHERE record_id=OLD.id;
 DELETE FROM fact_relations WHERE record_id=OLD.id;
 DELETE FROM fact_snapshots WHERE record_id=OLD.id;
 DELETE FROM fact_projection WHERE record_id=OLD.id;
 DELETE FROM fact_entities WHERE NOT EXISTS
  (SELECT 1 FROM fact_evidence e WHERE e.fact_id=fact_entities.id);
 DELETE FROM fact_relations WHERE NOT EXISTS
  (SELECT 1 FROM fact_entities f WHERE f.id=fact_relations.target_fact);
END;
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def migrate(connection) -> None:
    connection.executescript(SCHEMA)
    connection.execute("INSERT OR IGNORE INTO fact_meta VALUES('history_since',?)", (now(),))
