#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_start_where_they_say() {
        assert_eq!(span("a").start(), Position::new(11, 50));
    }
}
