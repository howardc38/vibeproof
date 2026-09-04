#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_selector() {
        assert_eq!(parse("a").is_ok(), true);
    }

    #[should_panic]
    fn rejects_a_bad_selector() { assert!(parse("!").is_err()); }
}
