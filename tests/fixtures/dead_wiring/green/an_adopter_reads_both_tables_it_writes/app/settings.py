def limits(cfg):
    """The config keys this repo actually reads, so the dial has a hand on it."""
    return (cfg["thresholds"]["min_chars"], cfg["test_command"], cfg["policy"])
