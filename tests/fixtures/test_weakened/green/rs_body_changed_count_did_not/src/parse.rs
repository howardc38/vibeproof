#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_selector() {
        let r = parse("a");
        assert!(r.is_ok(), "{:?}", r);
    }

    #[test]
    fn rejects_a_bad_selector() {
        assert!(parse("!").is_err());
    }
}
