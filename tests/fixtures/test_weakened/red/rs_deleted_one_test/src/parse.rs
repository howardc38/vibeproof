#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_selector() {
        assert_eq!(parse("a").is_ok(), true);
    }
}
