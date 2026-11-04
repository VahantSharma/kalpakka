"""Production `CallScorer`: the post-call judge (Evon, thinking on) behind `run_judge`."""

from kalpakka.brain import Prompt
from kalpakka.judge_runner import JudgeInput, JudgeRun, run_judge
from kalpakka.llm import LLMClient
from kalpakka.tripwire import Catalogue, LexiconTripwire


class JudgeScorer:
    def __init__(
        self,
        llm: LLMClient,
        prompt: Prompt,
        *,
        catalogue: Catalogue,
        tripwire: LexiconTripwire,
    ) -> None:
        self._llm = llm
        self._prompt = prompt
        self._catalogue = catalogue
        self._tripwire = tripwire

    async def score(self, data: JudgeInput) -> JudgeRun:
        return await run_judge(
            self._llm, self._prompt, data, catalogue=self._catalogue, tripwire=self._tripwire
        )
