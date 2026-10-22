"""Experimental metadata and conservative source/animal/batch split lineage."""
import hashlib
import json

FIELDS = ("experiment_id", "biological_replicate", "imaging_session", "animal_id", "exposure",
          "gain", "export_brightness", "acquisition_settings_confirmed", "spatial_calibration", "notes")


def template():
    return dict(schema_version=1, dataset={key: None for key in FIELDS}, groups={}, images={})


def metadata(run):
    path = run / "batch_metadata.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else template()


def resolve(document, src):
    value = dict(document.get("dataset", {}))
    value.update(document.get("groups", {}).get(src["group"], {}))
    value.update(document.get("images", {}).get(src["image_id"], {}))
    return value


def lineages(run, inventory, document=None):
    """Never split identical sources, declared animals, acquisition sessions or experiment replicates."""
    document = metadata(run) if document is None else document
    roots = {s["image_id"]: s["image_id"] for s in inventory}
    owners = {}
    resolved = {s["image_id"]: resolve(document, s) for s in inventory}

    def root(key):
        while roots[key] != key:
            roots[key] = roots[roots[key]]
            key = roots[key]
        return key

    for source in inventory:
        identifier = source["image_id"]
        info = resolved[identifier]
        tokens = ["source:" + source["source_sha256"]]
        experiment = info.get("experiment_id") or ""
        for field in ("animal_id", "imaging_session", "split_group"):
            if info.get(field):
                tokens.append(field + ":" + str(experiment) + ":" + str(info[field]))
        if experiment and info.get("biological_replicate"):
            tokens.append("replicate:" + str(experiment) + ":" + str(info["biological_replicate"]))
        for token in tokens:
            if token in owners:
                roots[root(identifier)] = root(owners[token])
            else:
                owners[token] = identifier
    components = {}
    for source in inventory:
        components.setdefault(root(source["image_id"]), []).append(source)
    result = {}
    for sources in components.values():
        splits = {(resolved[s["image_id"]].get("split") or "UNASSIGNED").upper() for s in sources}
        if not splits <= {"TRAIN", "VAL", "TEST", "UNASSIGNED"}:
            raise ValueError("Split must be TRAIN, VAL, TEST or UNASSIGNED")
        assigned = splits - {"UNASSIGNED"}
        if len(assigned) > 1:
            raise ValueError("Data leakage: same source/animal/session/replicate assigned to different splits: " +
                             ", ".join(s["image_id"] for s in sources))
        hashes = sorted({s["source_sha256"] for s in sources})
        group = "lineage_" + hashlib.sha256(json.dumps(hashes).encode()).hexdigest()[:16]
        for source in sources:
            info = resolved[source["image_id"]]
            result[source["image_id"]] = dict(SourceImageID=source["image_id"], SourceSHA256=source["source_sha256"],
                ExperimentID=info.get("experiment_id") or "", BiologicalReplicate=info.get("biological_replicate") or "",
                ImagingSession=info.get("imaging_session") or "", AnimalID=info.get("animal_id") or "",
                SplitGroup=group, Split=next(iter(assigned), "UNASSIGNED"))
    return result
