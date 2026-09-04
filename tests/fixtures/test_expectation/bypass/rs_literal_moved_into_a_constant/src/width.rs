#[cfg(test)]
mod tests {
    use super::*;
    const WIDE: usize = 43;

    #[test]
    fn measures_a_glyph() {
        assert_eq!(width("a"), WIDE);
    }

    #[test]
    fn measures_a_pair() {
        assert_eq!(width("ab"), 84);
    }
}
