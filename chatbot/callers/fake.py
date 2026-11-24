"""가짜 호출기: 정해 둔 이벤트를 그대로 내보낸다. 네트워크·비용 없이 계약 테스트와 eval(C7)에 쓴다."""

from chatbot.llm_caller import BaseCaller


class FakeCaller(BaseCaller):
    # 트레이스·로그에서 어느 공급자였는지 구분하는 이름
    provider = "fake"

    def __init__(self, responses, model=None):
        # 부모(BaseCaller)의 상태(_opened, _current)를 먼저 초기화한다. 빠뜨리면 stream()에서 AttributeError.
        super().__init__(model=model)
        # responses: 호출 한 번마다 내보낼 이벤트 목록의 목록. 예) [[e1, e2], [e3]] = 첫 호출 e1,e2 / 두 번째 e3
        # list()로 복사한다: pop으로 꺼내 쓰므로, 테스트가 넘긴 원본 리스트를 망가뜨리지 않게
        self._responses = list(responses)
        # 공급자가 받은 프롬프트 기록. 호출기가 무엇을 보냈는지 테스트가 확인할 수 있게 공개한다.
        self.prompts = []

    async def _open(self, prompt):
        # 받은 것부터 기록한다: 응답이 없어 아래 pop이 실패해도 무엇을 받았는지는 남는다
        self.prompts.append(prompt)
        # 호출 한 번 = 응답 목록 하나. 앞에서부터 꺼낸다.
        events = self._responses.pop(0)
        for event in events:
            yield event
