#  Copyright (c) 2025. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import json
from typing import List

from langchain_core.output_parsers import PydanticOutputParser


class CustomPydanticOutputParser(PydanticOutputParser):
    """Custom parser to modify the schema before getting format instructions."""

    major_error_type_list: List[str] = None
    minor_error_type_list: List[str] = None
    feedback_type_list: List[str] = None

    def get_format_instructions(self) -> str:
        # Copy schema to avoid altering original Pydantic schema.
        # schema = {k: v for k, v in self.pydantic_object.model_json_schema().items()}
        schema = dict(self.pydantic_object.model_json_schema().items())

        # Remove extraneous fields.
        reduced_schema = schema
        if "title" in reduced_schema:
            del reduced_schema["title"]
        if "type" in reduced_schema:
            del reduced_schema["type"]

        # Modify the schema to filter errors to the ones we want.
        if "$defs" in reduced_schema:
            if "MajorErrorType" in reduced_schema["$defs"]:
                reduced_schema["$defs"].get("MajorErrorType")["enum"] = self.major_error_type_list
            if "MinorErrorType" in reduced_schema["$defs"]:
                reduced_schema["$defs"].get("MinorErrorType")["enum"] = self.minor_error_type_list
            if "FeedbackType" in reduced_schema["$defs"]:
                reduced_schema["$defs"].get("FeedbackType")["enum"] = self.feedback_type_list

        # Ensure json in context is well-formed with double quotes.
        schema_str = json.dumps(reduced_schema, ensure_ascii=False)

        # Print to the console all the reduced_schema["properties"]
        print("Reduced Schema Properties: ", schema_str)

        return _PYDANTIC_FORMAT_INSTRUCTIONS.format(schema=schema_str)


_PYDANTIC_FORMAT_INSTRUCTIONS = """The output should be formatted as a JSON instance that conforms to the JSON schema below.

As an example, for the schema {{"properties": {{"foo": {{"title": "Foo", "description": "a list of strings", "type": "array", "items": {{"type": "string"}}}}}}, "required": ["foo"]}}
the object {{"foo": ["bar", "baz"]}} is a well-formatted instance of the schema. The object {{"properties": {{"foo": ["bar", "baz"]}}}} is not well-formatted.

Here is the output schema:
```
{schema}
```"""  # noqa: E501
