#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measures_a_glyph() {
        assert_eq!(width("a"), 42);
    }

    #[test]
    fn measures_a_pair() {
        assert_ne!(width("ab"), 85);
    }
}
