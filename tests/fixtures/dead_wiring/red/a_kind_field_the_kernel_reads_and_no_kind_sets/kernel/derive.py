"""The one interesting line: a claim-kind field this kernel reads."""


def derive(kind_cfg):
    return kind_cfg.get("depends_on_kind")
