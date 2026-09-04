#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measures_a_glyph() {
        assert!(width("a") >= 42);
    }

    #[test]
    fn measures_a_pair() {
        assert_eq!(width("ab"), 84);
    }
}
