def test_main_module_imports():
    # 어떤 테스트도 main.py를 import하지 않으면 진입점이 문법 오류여도 테스트가 모두 통과한다.
    # __main__ 가드 덕분에 import만으로는 봇이 시작되지 않는다.
    import main  # noqa: F401
