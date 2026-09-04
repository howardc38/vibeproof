#[cfg(test)]
mod tests {
    use super::*;
    use test_case::test_case;

    #[test_case("a",  true  ; "plain")]
    #[test_case("!",  false ; "invalid")]
    fn parses(input: &str, ok: bool) {
        assert_eq!(parse(input).is_ok(), ok);
    }
}
