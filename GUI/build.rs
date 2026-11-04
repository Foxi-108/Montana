fn main() {
    slint_build::compile("ui/main.slint")
        .expect("Не удалось скомпилировать интерфейс Slint");
}
