#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_selector() {
        assert_eq!(parse("a").is_ok(), true);
    }

    macro_rules! rejects {
        ($name:ident, $input:expr) => {
            #[test]
            fn $name() { assert!(parse($input).is_err()); }
        };
    }
}
