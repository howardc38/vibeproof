#[cfg(test)]
mod tests {
    use super::*;
    use insta::assert_snapshot;

    #[test]
    fn renders_a_path() {
        assert_snapshot!(render("M0 0 L1 1"));
    }
}
