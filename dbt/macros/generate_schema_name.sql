{#- Every model lands in the target schema (analytics by default), not "<target>_<custom>". -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ target.schema }}
{%- endmacro %}
