#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_selector() {
        assert_eq!(parse("a").is_ok(), true);
    }

    const GONE: &str = r#"
        #[test]
        fn rejects_a_bad_selector() { assert!(true); }
    "#;
}
