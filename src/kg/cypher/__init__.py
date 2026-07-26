from kg.cypher.parser import parse, CypherError, AST

__all__ = ["parse", "CypherError", "AST"]


def __getattr__(name):
    if name == "translate":
        from kg.cypher.translator import translate
        return translate
    raise AttributeError(name)
