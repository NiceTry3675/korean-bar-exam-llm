"""
@brief 벤치마크 레지스트리 기반 Excel/JSON 동기화 회귀 테스트
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sync_data import ExcelHandler, PathMapper, SyncManager


REPO_ROOT = Path(__file__).resolve().parents[1]
BAR_WORKBOOK = REPO_ROOT / '제15회 변호사시험 LLM 풀이.xlsx'


def _copy_bar_template(target_path: Path) -> None:
    """
    @brief 모델 결과 컬럼을 제거한 빈 변호사시험 워크북 사본을 만든다.

    저장소 워크북에는 실제 모델 결과가 채워져 있으므로, 빈 템플릿을
    가정하는 테스트는 이 사본을 사용한다.

    @param target_path 템플릿 사본을 저장할 경로
    """
    shutil.copyfile(BAR_WORKBOOK, target_path)
    mapper = PathMapper(benchmark_id='bar-exam-15')
    handler = ExcelHandler(target_path, mapper)
    for sheet_name in ('공법', '민사법', '형사법'):
        columns = sorted(handler.get_model_columns(sheet_name).values(), reverse=True)
        worksheet = handler.workbook[sheet_name]
        for column_index in columns:
            worksheet.delete_cols(column_index)
    handler.save()


class PathMapperTests(unittest.TestCase):
    """@brief 공개 변호사시험 메타데이터 로딩을 검증한다."""

    def test_bar_exam_public_metadata_is_sync_compatible(self):
        """@brief 로컬 문제 본문 없이도 150문항과 375점을 읽는다."""
        mapper = PathMapper(
            base_dir=REPO_ROOT,
            benchmark_id='bar-exam-15',
            registry_path=Path('benchmarks/registry.json'),
        )

        counts = []
        points = []
        for sheet_name in mapper.get_all_sheets():
            questions = mapper.load_questions(sheet_name)['questions']
            counts.append(len(questions))
            points.append(sum(question['points'] for question in questions))

        self.assertEqual(counts, [40, 70, 40])
        self.assertEqual(points, [100, 175, 100])

    def test_public_answer_metadata_overrides_stale_local_copy(self):
        """@brief sync 채점은 ignored 로컬 사본보다 공개 최종정답을 우선한다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root = Path(temp_dir_name)
            public_path = root / 'benchmarks' / 'bar' / 'questions.json'
            local_path = root / 'problems' / 'bar' / 'public' / 'questions.json'
            public_path.parent.mkdir(parents=True)
            local_path.parent.mkdir(parents=True)
            public_path.write_text(json.dumps({
                'benchmark_id': 'bar-exam-15',
                'subjects': [{
                    'id': 'public-law',
                    'subject': '공법',
                    'section': '공법',
                    'sheet_name': '공법',
                    'questions': [{
                        'number': 1,
                        'correct_answer': 5,
                        'points': 2.5,
                    }],
                }],
            }, ensure_ascii=False), encoding='utf-8')
            local_path.write_text(json.dumps({
                'subject': '공법',
                'section': '공법',
                'questions': [{
                    'number': 1,
                    'correct_answer': 1,
                    'points': 2.5,
                }],
            }, ensure_ascii=False), encoding='utf-8')
            registry_path = root / 'benchmarks' / 'registry.json'
            registry_path.write_text(json.dumps({
                'benchmarks': [{
                    'id': 'bar-exam-15',
                    'sections': [{
                        'id': 'public-law',
                        'sheet': '공법',
                        'subject': '공법',
                        'section': '공법',
                        'problemDir': 'problems/bar/public',
                        'metadataPath': 'benchmarks/bar/questions.json',
                    }],
                }],
            }, ensure_ascii=False), encoding='utf-8')

            mapper = PathMapper(
                base_dir=root,
                benchmark_id='bar-exam-15',
                registry_path=Path('benchmarks/registry.json'),
            )
            questions = mapper.load_questions('공법')['questions']
            self.assertEqual(questions[0]['correct_answer'], 5)


class SyncManagerTests(unittest.TestCase):
    """@brief 2.5점 단위와 배점 고정열 처리를 검증한다."""

    @staticmethod
    def _result_payload(mapper, sheet_name, model_name='Fixture Model',
                        wrong_last=False, answer_status='answered'):
        """@brief 공개 메타데이터와 일치하는 완전한 검증 결과를 만든다."""
        questions = mapper.load_questions(sheet_name)['questions']
        results = []
        score = 0.0
        for question in questions:
            if answer_status == 'parse_failed':
                extracted = None
            else:
                extracted = question['correct_answer']
                if wrong_last and question['number'] == questions[-1]['number']:
                    extracted = 1 if extracted != 1 else 2
            is_correct = extracted == question['correct_answer']
            if is_correct:
                score += question['points']
            results.append({
                'model_name': model_name,
                'question_number': question['number'],
                'extracted_answer': extracted,
                'correct_answer': question['correct_answer'],
                'is_correct': is_correct,
                'points': question['points'],
                'answer_status': answer_status,
                'provider_stop_reason': 'stop',
            })

        return {
            'benchmark_id': 'bar-exam-15',
            'run_mode': 'question',
            'sheet_name': sheet_name,
            'subject': sheet_name,
            'section': sheet_name,
            'model_scores': {model_name: score},
            'results': results,
        }

    def test_empty_bar_workbook_has_no_model_columns(self):
        """@brief 배점 열을 모델로 오인하지 않고 공식 만점을 읽는다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            workbook_path = Path(temp_dir_name) / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)
            mapper = PathMapper(benchmark_id='bar-exam-15')
            handler = ExcelHandler(workbook_path, mapper)

            self.assertEqual(handler.get_model_columns('공법'), {})
            self.assertEqual(handler.get_max_score('공법'), 100)
            self.assertEqual(handler.get_max_score('민사법'), 175)

    def test_partial_checkpoint_cannot_be_imported(self):
        """@brief 중간 실행 결과를 나머지 문항 포기로 확정하지 않는다."""
        manager = SyncManager(
            excel_path=BAR_WORKBOOK,
            benchmark_id='bar-exam-15',
        )
        partial = {
            'model_scores': {'Fixture Model': 2.5},
            'results': [{
                'model_name': 'Fixture Model',
                'question_number': 1,
            }],
        }
        self.assertFalse(manager._has_complete_model_results(partial, '공법'))

    def test_import_and_export_preserve_fractional_score(self):
        """@brief 39문항 정답인 공법 결과가 97.5점으로 왕복된다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)

            mapper = PathMapper(benchmark_id='bar-exam-15')
            model_name = 'Fixture Model'
            payload = self._result_payload(
                mapper, '공법', model_name=model_name, wrong_last=True
            )
            score = payload['model_scores'][model_name]

            result_path = temp_dir / 'results_verified.json'
            payload['run_metadata'] = {
                    model_name: {
                        'provider': 'openai-compatible',
                        'model_id': 'fixture-model',
                        'token_usage': {
                            'input_tokens': 1000,
                            'output_tokens': 250,
                            'total_tokens': 1250,
                        },
                        'cost_usd': 0.0045,
                        'pricing': {
                            'input_per_million': 2.0,
                            'output_per_million': 10.0,
                        },
                        'updated_at': '2026-07-21T00:00:00Z',
                    }
                }
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding='utf-8'
            )

            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            manager.token_usage_path = temp_dir / 'bar_exam_token_usage.json'
            manager._token_usage = {'models': {}}
            self.assertTrue(manager.import_from_json(result_path))
            manager.excel_handler.save()
            self.assertEqual(manager.excel_handler.get_model_score('공법', model_name), 97.5)

            usage = json.loads(manager.token_usage_path.read_text(encoding='utf-8'))
            usage_model = usage['models'][model_name]
            self.assertEqual(usage_model['sections']['공법']['total_tokens'], 1250)
            self.assertEqual(usage_model['sections']['공법']['cost_usd'], 0.0045)
            self.assertEqual(usage_model['price'], {'input': 2.0, 'output': 10.0})

            partial_export_path = temp_dir / 'partial-aggregate.json'
            manager.export_all_sheets_to_json(partial_export_path, all_models=True)
            self.assertEqual(
                json.loads(partial_export_path.read_text(encoding='utf-8')),
                [],
            )

            for sheet_name in ('민사법', '형사법'):
                section_payload = self._result_payload(
                    mapper, sheet_name, model_name=model_name
                )
                answers = {
                    result['question_number']: result['extracted_answer']
                    for result in section_payload['results']
                }
                manager.excel_handler.add_model_column(
                    sheet_name,
                    model_name,
                    answers,
                    section_payload['model_scores'][model_name],
                )
            manager.excel_handler.save()

            export_path = temp_dir / 'aggregate.json'
            manager.export_all_sheets_to_json(export_path, all_models=True)
            exported = json.loads(export_path.read_text(encoding='utf-8'))
            self.assertEqual(len(exported), 3)
            public = next(item for item in exported if item['sheet_name'] == '공법')
            self.assertEqual(public['score'], 97.5)
            self.assertEqual(public['correct_count'], 39)
            self.assertEqual(public['benchmark_id'], 'bar-exam-15')
            self.assertEqual(public['run_mode'], 'question')
            self.assertEqual(public['token_usage']['cost_usd'], 0.0045)
            self.assertEqual(public['price'], {'input': 2.0, 'output': 10.0})
            self.assertEqual(sum(item['score'] for item in exported), 372.5)

    def test_lenient_v2_stays_in_excel_and_never_reaches_web_export(self):
        """@brief v2 병행 표기는 Excel 전용이며 대시보드 export에서 제외된다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)

            mapper = PathMapper(benchmark_id='bar-exam-15')
            model_name = 'Fixture Model'
            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            manager.token_usage_path = temp_dir / 'bar_exam_token_usage.json'
            manager._token_usage = {'models': {}}

            for sheet_name in ('공법', '민사법', '형사법'):
                payload = self._result_payload(
                    mapper, sheet_name, model_name=model_name, wrong_last=True
                )
                # 마지막 문항만 strict 파서가 못 읽고 lenient 파서가 정답을 복원한 상황
                last = payload['results'][-1]
                last['extracted_answer'] = None
                last['answer_status'] = 'parse_failed'
                last['is_correct'] = False
                last['lenient_answer'] = last['correct_answer']
                last['lenient_status'] = 'answered'
                last['lenient_is_correct'] = True
                strict_score = sum(
                    row['points'] for row in payload['results'] if row['is_correct']
                )
                payload['model_scores'] = {model_name: strict_score}
                payload['model_scores_lenient'] = {
                    model_name: strict_score + last['points']
                }
                source_path = temp_dir / f'{sheet_name}.json'
                source_path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding='utf-8'
                )
                self.assertTrue(manager.import_from_json(source_path))
            manager.excel_handler.save()

            for sheet_name in ('공법', '민사법', '형사법'):
                columns = manager.excel_handler.get_model_columns(sheet_name)
                self.assertIn(model_name, columns)
                self.assertIn(f'{model_name} (v2)', columns)

            export_path = temp_dir / 'aggregate.json'
            manager.export_all_sheets_to_json(export_path, all_models=True)
            exported = json.loads(export_path.read_text(encoding='utf-8'))
            self.assertEqual(
                [], [item for item in exported if item['model_name'].endswith(' (v2)')]
            )
            self.assertEqual({model_name}, {item['model_name'] for item in exported})
            # 공식 점수는 strict(v1) 97.5점이며, lenient가 복원한 100.0점이 아니다.
            public = next(item for item in exported if item['sheet_name'] == '공법')
            self.assertEqual(97.5, public['score'])

    def test_lenient_scores_are_exported_as_side_fields(self):
        """@brief v2 점수는 공식 점수를 건드리지 않고 부가 필드로만 노출된다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)

            mapper = PathMapper(benchmark_id='bar-exam-15')
            model_name = 'Fixture Model'
            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            manager.token_usage_path = temp_dir / 'bar_exam_token_usage.json'
            manager._token_usage = {'models': {}}

            expected = {}
            for sheet_name in ('공법', '민사법', '형사법'):
                payload = self._result_payload(
                    mapper, sheet_name, model_name=model_name, wrong_last=True
                )
                # 마지막 문항만 strict 파서가 못 읽고 lenient 파서가 정답을 복원한 상황
                last = payload['results'][-1]
                last['extracted_answer'] = None
                last['answer_status'] = 'parse_failed'
                last['is_correct'] = False
                last['lenient_answer'] = last['correct_answer']
                last['lenient_status'] = 'answered'
                last['lenient_is_correct'] = True
                strict_score = sum(
                    row['points'] for row in payload['results'] if row['is_correct']
                )
                strict_correct = sum(1 for row in payload['results'] if row['is_correct'])
                payload['model_scores'] = {model_name: strict_score}
                payload['model_scores_lenient'] = {
                    model_name: strict_score + last['points']
                }
                payload['model_metrics'] = {
                    model_name: {
                        'lenient_score': strict_score + last['points'],
                        'lenient_correct_count': strict_correct + 1,
                    }
                }
                expected[sheet_name] = {
                    'score': strict_score,
                    'score_lenient': strict_score + last['points'],
                    'correct_count': strict_correct,
                    'correct_count_lenient': strict_correct + 1,
                    'last_question': last['question_number'],
                    'lenient_answer': last['correct_answer'],
                }
                source_path = temp_dir / f'{sheet_name}.json'
                source_path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding='utf-8'
                )
                self.assertTrue(manager.import_from_json(source_path))
            manager.excel_handler.save()

            # 검증 결과 JSON을 임시 디렉터리에서 읽도록 대체한다
            manager._get_verified_json_file = (
                lambda sheet_name: temp_dir / f'{sheet_name}.json'
            )

            export_path = temp_dir / 'aggregate.json'
            manager.export_all_sheets_to_json(export_path, all_models=True)
            exported = json.loads(export_path.read_text(encoding='utf-8'))

            self.assertEqual({model_name}, {item['model_name'] for item in exported})
            for sheet_name, values in expected.items():
                record = next(
                    item for item in exported if item['sheet_name'] == sheet_name
                )
                # 공식 점수는 strict(v1) 그대로다
                self.assertEqual(values['score'], record['score'])
                self.assertEqual(values['correct_count'], record['correct_count'])
                self.assertEqual(values['score_lenient'], record['score_lenient'])
                self.assertEqual(
                    values['correct_count_lenient'], record['correct_count_lenient']
                )

                # lenient 결과가 다른 문항에만 부가 필드가 붙는다
                rows_with_lenient = [
                    row for row in record['results'] if 'lenient_answer' in row
                ]
                self.assertEqual(1, len(rows_with_lenient))
                self.assertEqual(
                    values['last_question'], rows_with_lenient[0]['question_number']
                )
                self.assertEqual(
                    values['lenient_answer'], rows_with_lenient[0]['lenient_answer']
                )
                self.assertTrue(rows_with_lenient[0]['lenient_is_correct'])

    def test_lenient_export_tolerates_missing_verified_file(self):
        """@brief 검증 결과 파일이 없어도 export는 v1 필드만으로 성공한다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)

            mapper = PathMapper(benchmark_id='bar-exam-15')
            model_name = 'Fixture Model'
            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            manager.token_usage_path = temp_dir / 'bar_exam_token_usage.json'
            manager._token_usage = {'models': {}}
            manager._get_verified_json_file = (
                lambda sheet_name: temp_dir / 'absent' / f'{sheet_name}.json'
            )

            for sheet_name in ('공법', '민사법', '형사법'):
                payload = self._result_payload(
                    mapper, sheet_name, model_name=model_name
                )
                answers = {
                    row['question_number']: row['extracted_answer']
                    for row in payload['results']
                }
                manager.excel_handler.add_model_column(
                    sheet_name,
                    model_name,
                    answers,
                    payload['model_scores'][model_name],
                )
            manager.excel_handler.save()

            export_path = temp_dir / 'aggregate.json'
            manager.export_all_sheets_to_json(export_path, all_models=True)
            exported = json.loads(export_path.read_text(encoding='utf-8'))

            self.assertEqual(3, len(exported))
            for record in exported:
                self.assertNotIn('score_lenient', record)
                self.assertNotIn('correct_count_lenient', record)
                self.assertTrue(all(
                    'lenient_answer' not in row for row in record['results']
                ))

    def test_parse_failed_round_trip_preserves_manual_review(self):
        """@brief 파싱 실패가 포기로 바뀌지 않고 검토 상태로 왕복된다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)
            mapper = PathMapper(benchmark_id='bar-exam-15')
            payload = self._result_payload(
                mapper, '공법', answer_status='parse_failed'
            )
            source_path = temp_dir / 'parse-failed.json'
            source_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding='utf-8'
            )

            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            self.assertTrue(manager.import_from_json(source_path))
            manager.excel_handler.save()

            exported_path = temp_dir / 'round-trip.json'
            manager.export_to_json('공법', 'Fixture Model', exported_path)
            exported = json.loads(exported_path.read_text(encoding='utf-8'))
            self.assertEqual(exported['manual_review_count'], 40)
            first = exported['results'][0]
            self.assertIsNone(first['extracted_answer'])
            self.assertEqual(first['answer_status'], 'parse_failed')
            self.assertTrue(first['needs_manual_review'])

    def test_mode_mismatch_is_rejected(self):
        """@brief 문항별 결과를 과목 일괄 워크북에 잘못 가져오지 않는다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)
            mapper = PathMapper(benchmark_id='bar-exam-15')
            source_path = temp_dir / 'wrong-mode.json'
            source_path.write_text(
                json.dumps(
                    self._result_payload(mapper, '공법'),
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )

            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
                hard_mode=True,
            )
            self.assertFalse(manager.import_from_json(source_path))
            self.assertEqual(manager.excel_handler.get_model_columns('공법'), {})

    def test_declared_score_mismatch_is_rejected(self):
        """@brief 원문 정답으로 재계산한 점수와 다른 결과를 거부한다."""
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            workbook_path = temp_dir / BAR_WORKBOOK.name
            _copy_bar_template(workbook_path)
            mapper = PathMapper(benchmark_id='bar-exam-15')
            payload = self._result_payload(mapper, '공법')
            payload['model_scores']['Fixture Model'] = 0
            source_path = temp_dir / 'wrong-score.json'
            source_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding='utf-8'
            )

            manager = SyncManager(
                excel_path=workbook_path,
                benchmark_id='bar-exam-15',
            )
            self.assertFalse(manager.import_from_json(source_path))
            self.assertEqual(manager.excel_handler.get_model_columns('공법'), {})


if __name__ == '__main__':
    unittest.main()
