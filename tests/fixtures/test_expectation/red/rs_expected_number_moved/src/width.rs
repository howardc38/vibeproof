#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn measures_a_glyph() {
        assert_eq!(width("a"), 43);
    }

    #[test]
    fn measures_a_pair() {
        assert_eq!(width("ab"), 84);
    }
}
