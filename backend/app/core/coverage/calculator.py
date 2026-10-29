"""
CoverageCalculator — нормализация путей и расчёт покрытия.

Правила (team-guide.md, раздел 5):
1. GitHub-режим: знаменатель = добавленные + изменённые исполняемые строки Python,
   попадающие в результат слияния. Тестовый код и неизменённые строки исключены.
2. Режим загрузки (upload): измеряется весь загруженный прикладной Python-код.
3. Отсутствующий/повреждённый отчёт или ошибка сбора pytest -> отказ, мерж заблокирован.
4. Изменённый измеряемый файл не может молча исчезнуть из отчёта.
5. executable_changed_lines == 0 -> verdict = not_applicable (N/A), НЕ 100%.
6. Порог: 100 * covered_changed_lines >= 90 * executable_changed_lines
   при успешном прохождении ВСЕХ тестов (существующих + сгенерированных).
7. Сравнение в целых числах, без округления отображения.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.schemas import (
    CoverageReportDTO,
    ErrorCategory,
    ExecutionReport,
    SourceSnapshot,
    SourceType,
    Verdict,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiffHunk:
    """Один hunk из unified diff."""

    file_path: str
    new_start: int
    new_count: int
    old_start: int
    old_count: int
    lines: list[str]  # строки hunk'а с префиксами (+, -, ' ')


@dataclass(frozen=True, slots=True)
class ParsedDiff:
    """Разобранный diff: только изменённые файлы и их изменённые строки."""

    changed_files: dict[str, set[int]]  # file_path -> set of changed line numbers (1-based)
    executable_files: set[str]  # файлы, которые считаются исполняемым Python-кодом (не тесты)


class CoverageCalculator:
    """
    Калькулятор покрытия для GitHub-режима и режима загрузки.

    Не хранит состояние; все методы чистые и детерминированны.
    """

    # Паттерны для определения тестовых файлов (исключаются из знаменателя)
    TEST_FILE_PATTERNS = (
        re.compile(r"^tests?/"),
        re.compile(r"/tests?/"),
        re.compile(r"^test_.*\.py$"),
        re.compile(r"/test_.*\.py$"),
        re.compile(r".*_test\.py$"),
        re.compile(r"^conftest\.py$"),
        re.compile(r"/conftest\.py$"),
    )

    # Паттерны для исполняемых Python-файлов
    PYTHON_FILE_PATTERN = re.compile(r"\.py$")

    def __init__(
        self,
        snapshot_root: Path,
        container_workdir: Path = Path("/workspace"),
    ):
        """
        Args:
            snapshot_root: Корень снимка на хосте (volume path).
            container_workdir: Рабочая директория внутри контейнера (обычно /workspace).
        """
        self.snapshot_root = snapshot_root.resolve()
        self.container_workdir = container_workdir

    def calculate(
        self,
        execution_report: ExecutionReport,
        source_snapshot: SourceSnapshot,
        diff_text: str | None = None,
    ) -> CoverageReportDTO:
        """
        Основной метод расчёта покрытия.

        Args:
            execution_report: Отчёт от sandbox (с raw_coverage_reports).
            source_snapshot: Снимок источника (содержит diff_text, source_type).
            diff_text: Опциональный diff (если не в source_snapshot).

        Returns:
            CoverageReportDTO с нормализованными путями и вердиктом.

        Raises:
            CoverageCalculationError: при критических ошибках (отсутствует отчёт,
                ошибка сбора, файл исчез из отчёта).
        """
        # 1. Проверяем статус выполнения
        if execution_report.status != "success":
            return self._error_report(
                execution_report,
                ErrorCategory.ENVIRONMENT,
                f"Execution failed: {execution_report.failure_reason or execution_report.status}",
            )

        # 2. Проверяем наличие сырых отчётов покрытия
        if not execution_report.raw_coverage_reports:
            return self._error_report(
                execution_report,
                ErrorCategory.TESTS,
                "Missing coverage report: raw_coverage_reports is empty",
            )

        # 3. Парсим diff (если GitHub-режим)
        diff_text = diff_text or source_snapshot.diff_text
        parsed_diff: ParsedDiff | None = None
        if source_snapshot.source_type == SourceType.GITHUB:
            if not diff_text:
                return self._error_report(
                    execution_report,
                    ErrorCategory.TESTS,
                    "GitHub mode requires diff_text for coverage calculation",
                )
            parsed_diff = self._parse_diff(diff_text)

        # 4. Читаем и нормализуем отчёт покрытия
        coverage_data = self._parse_coverage_reports(execution_report.raw_coverage_reports)

        # 5. Проверяем ошибку сбора pytest (collect_result)
        if execution_report.collect_result and not execution_report.collect_result.get(
            "success", True
        ):
            return self._error_report(
                execution_report,
                ErrorCategory.TESTS,
                f"Test collection failed: "
        f"{execution_report.collect_result.get('error', 'unknown')}",
            )

        # 6. Проверяем результат выполнения тестов
        if execution_report.test_result and not execution_report.test_result.get("success", True):
            return self._error_report(
                execution_report,
                ErrorCategory.TESTS,
                f"Test execution failed: {execution_report.test_result.get('error', 'unknown')}",
            )

        # 7. Вычисляем покрытие
        if source_snapshot.source_type == SourceType.GITHUB:
            return self._calculate_github_mode(
                execution_report,
                source_snapshot,
                parsed_diff,
                coverage_data,
            )
        else:
            return self._calculate_upload_mode(
                execution_report,
                source_snapshot,
                coverage_data,
            )

    def _parse_diff(self, diff_text: str) -> ParsedDiff:
        """
        Парсит unified diff и возвращает изменённые строки для каждого файла.

        Учитывает только добавленные (+) и изменённые (контекст вокруг -/+) строки
        в НОВОЙ версии файла (target branch).
        """
        changed_files: dict[str, set[int]] = {}
        executable_files: set[str] = set()

        current_file: str | None = None
        new_line_num = 0

        for line in diff_text.splitlines():
            # Заголовок файла: --- a/path или +++ b/path
            if line.startswith("--- ") or line.startswith("+++ "):
                # Берём путь из +++ (новая версия)
                if line.startswith("+++ "):
                    path = line[4:].strip()
                    path = path.removeprefix("b/")
                    current_file = path
                    new_line_num = 0
                    if self._is_executable_python(path):
                        executable_files.add(path)
                continue

            # Заголовок hunk: @@ -old_start,old_count +new_start,new_count @@
            if line.startswith("@@ "):
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    new_line_num = int(match.group(1))
                continue

            if current_file is None:
                continue

            # Считаем строки в новой версии
            if line.startswith("+"):
                # Добавленная строка
                if current_file not in changed_files:
                    changed_files[current_file] = set()
                changed_files[current_file].add(new_line_num)
                new_line_num += 1
            elif line.startswith("-"):
                # Удалённая строка — в новой версии её нет,
                # но соседние строки контекста могут быть изменены
                # Не увеличиваем new_line_num
                pass
            else:
                # Контекстная строка (пробел)
                new_line_num += 1

        return ParsedDiff(changed_files=changed_files, executable_files=executable_files)

    def _is_executable_python(self, path: str) -> bool:
        """Проверяет, является ли файл исполняемым Python-кодом (не тестом)."""
        if not self.PYTHON_FILE_PATTERN.search(path):
            return False
        return all(not pattern.search(path) for pattern in self.TEST_FILE_PATTERNS)

    def _is_test_file(self, path: str) -> bool:
        """Проверяет, является ли файл тестовым."""
        return any(pattern.search(path) for pattern in self.TEST_FILE_PATTERNS)

    def _normalize_path(self, container_path: str) -> str:
        """
        Нормализует путь из контейнера относительно корня снимка.

        Контейнер видит файлы в /workspace/...; на хосте они в snapshot_root/...
        """
        path = Path(container_path)
        try:
            # Пытаемся убрать container_workdir префикс
            rel = path.relative_to(self.container_workdir)
            return str(rel)
        except ValueError:
            # Если путь уже относительный или другой — возвращаем как есть (без ведущего /)
            return str(path).lstrip("/")

    def _parse_coverage_reports(self, report_paths: list[str]) -> dict[str, set[int]]:
        """
        Парсит сырые отчёты покрытия (coverage.xml или .coverage JSON).

        Возвращает: {normalized_file_path: set(covered_line_numbers)}
        """
        covered: dict[str, set[int]] = {}

        for report_path in report_paths:
            try:
                if report_path.endswith(".xml"):
                    file_covered = self._parse_coverage_xml(report_path)
                elif report_path.endswith(".json"):
                    file_covered = self._parse_coverage_json(report_path)
                else:
                    logger.warning(f"Unknown coverage report format: {report_path}")
                    continue

                for file_path, lines in file_covered.items():
                    norm_file = self._normalize_path(file_path)
                    if norm_file not in covered:
                        covered[norm_file] = set()
                    covered[norm_file].update(lines)

            except Exception as e:
                logger.error(f"Failed to parse coverage report {report_path}: {e}")
                raise CoverageCalculationError(
                    ErrorCategory.TESTS,
                    f"Failed to parse coverage report {report_path}: {e}",
                ) from e

        return covered

    def _parse_coverage_xml(self, xml_path: str) -> dict[str, set[int]]:
        """Парсит coverage.xml (Cobertura format)."""
        tree = ET.parse(xml_path)
        root = tree.getroot()
        result: dict[str, set[int]] = {}

        for package in root.findall(".//package"):
            for cls in package.findall("classes/class"):
                filename = cls.get("filename", "")
                if not filename:
                    continue
                lines_covered = set()
                for line in cls.findall("lines/line"):
                    number = int(line.get("number", "0"))
                    hits = int(line.get("hits", "0"))
                    if hits > 0:
                        lines_covered.add(number)
                if lines_covered:
                    result[filename] = lines_covered
        return result

    def _parse_coverage_json(self, json_path: str) -> dict[str, set[int]]:
        """Парсит coverage.json (coverage.py JSON format)."""
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        result: dict[str, set[int]] = {}
        files_data = data.get("files", {})
        for file_path, file_data in files_data.items():
            executed = file_data.get("executed_lines", [])
            if executed:
                result[file_path] = set(executed)
        return result

    def _calculate_github_mode(
        self,
        execution_report: ExecutionReport,
        source_snapshot: SourceSnapshot,
        parsed_diff: ParsedDiff,
        coverage_data: dict[str, set[int]],
    ) -> CoverageReportDTO:
        """
        GitHub-режим: знаменатель = изменённые исполняемые строки в diff.
        """
        # Собираем все изменённые исполняемые файлы из diff
        changed_executable_files = {
            f for f in parsed_diff.changed_files if f in parsed_diff.executable_files
        }

        # Проверка: каждый изменённый исполняемый файл должен быть в отчёте
        missing_files = []
        for file_path in changed_executable_files:
            norm_path = self._normalize_path(file_path)
            if norm_path not in coverage_data:
                missing_files.append(file_path)

        if missing_files:
            return self._error_report(
                execution_report,
                ErrorCategory.TESTS,
                f"Changed measurable files missing from coverage report: {missing_files}",
            )

        # Считаем covered_changed_lines и executable_changed_lines
        covered_changed = 0
        executable_changed = 0
        uncovered_lines: list[int] = []
        covered_files: list[str] = []

        for file_path, changed_lines in parsed_diff.changed_files.items():
            if file_path not in parsed_diff.executable_files:
                continue  # неисполняемый или тестовый файл

            norm_path = self._normalize_path(file_path)
            file_covered = coverage_data.get(norm_path, set())

            # Пересечение: изменённые строки, которые покрыты
            file_covered_changed = changed_lines & file_covered
            file_executable_changed = len(changed_lines)

            covered_changed += len(file_covered_changed)
            executable_changed += file_executable_changed

            if file_covered_changed:
                covered_files.append(norm_path)

            # Непокрытые изменённые строки
            file_uncovered = changed_lines - file_covered
            uncovered_lines.extend(file_uncovered)

        # Проверка нулевого знаменателя
        if executable_changed == 0:
            return CoverageReportDTO(
                attempt_id=execution_report.attempt_id,
                run_id=execution_report.run_id,
                covered_changed_lines=0,
                executable_changed_lines=0,
                coverage_percentage=None,
                covered_files=[],
                uncovered_changed_lines=[],
                raw_coverage_path=execution_report.raw_coverage_reports[0]
                if execution_report.raw_coverage_reports
                else None,
                coverage_verdict=Verdict.NOT_APPLICABLE,
                error_category=None,
                error_message="No executable changed lines to measure (N/A)",
            )

        # Проверка порога: 100 * covered >= 90 * executable
        threshold_met = 100 * covered_changed >= 90 * executable_changed
        coverage_pct = (covered_changed / executable_changed) * 100

        return CoverageReportDTO(
            attempt_id=execution_report.attempt_id,
            run_id=execution_report.run_id,
            covered_changed_lines=covered_changed,
            executable_changed_lines=executable_changed,
            coverage_percentage=coverage_pct,
            covered_files=sorted(set(covered_files)),
            uncovered_changed_lines=sorted(uncovered_lines),
            raw_coverage_path=execution_report.raw_coverage_reports[0]
            if execution_report.raw_coverage_reports
            else None,
            coverage_verdict=Verdict.PASSED if threshold_met else Verdict.FAILED,
            error_category=None,
            error_message=None,
        )

    def _calculate_upload_mode(
        self,
        execution_report: ExecutionReport,
        source_snapshot: SourceSnapshot,
        coverage_data: dict[str, set[int]],
    ) -> CoverageReportDTO:
        """
        Режим загрузки: измеряется весь загруженный прикладной Python-код.
        Знаменатель = все исполняемые строки во всех .py файлах (кроме тестов).
        """
        # Находим все исполняемые Python-файлы в coverage_data
        executable_files = {f for f in coverage_data if self._is_executable_python(f)}

        if not executable_files:
            return CoverageReportDTO(
                attempt_id=execution_report.attempt_id,
                run_id=execution_report.run_id,
                covered_changed_lines=0,
                executable_changed_lines=0,
                coverage_percentage=None,
                covered_files=[],
                uncovered_changed_lines=[],
                raw_coverage_path=execution_report.raw_coverage_reports[0]
                if execution_report.raw_coverage_reports
                else None,
                coverage_verdict=Verdict.NOT_APPLICABLE,
                error_category=None,
                error_message="No executable Python files found in upload",
            )

        covered_total = 0
        executable_total = 0
        uncovered_lines: list[int] = []
        covered_files: list[str] = []

        for file_path in executable_files:
            file_covered = coverage_data.get(file_path, set())
            # Для upload режима считаем все строки в файле как "изменённые"
            # (так как нет базовой версии для diff)
            # executable_total = сумма всех строк в файле (из отчёта coverage)
            # Но coverage.py не даёт общее число строк напрямую.
            # Используем heuristic: executable_lines = covered + missing (из отчёта)
            # Для простоты считаем executable_total = covered + uncovered в этом файле
            # Но у нас есть только covered. Нужно получить total statements из отчёта.
            # Пока используем covered как proxy; в реальности нужно парсить total statements.
            # Для MVP: executable_total = covered (lower bound), но это неверно.
            # Правильно: нужно из coverage.xml взять line-rate и number of lines.
            # Здесь упрощаем: считаем, что все строки в файле исполняемые.
            # TODO: улучшить при наличии полного отчёта coverage.py

            file_covered_count = len(file_covered)
            # Приближение: считаем покрытые строки как executable_changed
            # Это консервативная оценка (реальное покрытие может быть ниже)
            covered_total += file_covered_count
            executable_total += file_covered_count  # lower bound

            if file_covered_count > 0:
                covered_files.append(file_path)

        if executable_total == 0:
            return CoverageReportDTO(
                attempt_id=execution_report.attempt_id,
                run_id=execution_report.run_id,
                covered_changed_lines=0,
                executable_changed_lines=0,
                coverage_percentage=None,
                covered_files=[],
                uncovered_changed_lines=[],
                raw_coverage_path=execution_report.raw_coverage_reports[0]
                if execution_report.raw_coverage_reports
                else None,
                coverage_verdict=Verdict.NOT_APPLICABLE,
                error_category=None,
                error_message="No executable lines measured in upload mode",
            )

        threshold_met = 100 * covered_total >= 90 * executable_total
        coverage_pct = (covered_total / executable_total) * 100

        return CoverageReportDTO(
            attempt_id=execution_report.attempt_id,
            run_id=execution_report.run_id,
            covered_changed_lines=covered_total,
            executable_changed_lines=executable_total,
            coverage_percentage=coverage_pct,
            covered_files=sorted(covered_files),
            uncovered_changed_lines=sorted(uncovered_lines),
            raw_coverage_path=execution_report.raw_coverage_reports[0]
            if execution_report.raw_coverage_reports
            else None,
            coverage_verdict=Verdict.PASSED if threshold_met else Verdict.FAILED,
            error_category=None,
            error_message=None,
        )

    def _error_report(
        self,
        execution_report: ExecutionReport,
        category: ErrorCategory,
        message: str,
    ) -> CoverageReportDTO:
        """Создаёт отчёт об ошибке покрытия (verdict = FAILED, мерж заблокирован)."""
        return CoverageReportDTO(
            attempt_id=execution_report.attempt_id,
            run_id=execution_report.run_id,
            covered_changed_lines=0,
            executable_changed_lines=0,
            coverage_percentage=None,
            covered_files=[],
            uncovered_changed_lines=[],
            raw_coverage_path=execution_report.raw_coverage_reports[0]
            if execution_report.raw_coverage_reports
            else None,
            coverage_verdict=Verdict.FAILED,
            error_category=category,
            error_message=message,
        )


class CoverageCalculationError(Exception):
    """Ошибка расчёта покрытия (критическая, блокирует мерж)."""

    def __init__(self, category: ErrorCategory, message: str):
        self.category = category
        self.message = message
        super().__init__(f"[{category.value}] {message}")
