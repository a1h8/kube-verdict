from ontology.dynamic_entity import APIResourceInfo, GenericEntity, _flatten


class TestAPIResourceInfo:
    def test_api_version_core(self):
        info = APIResourceInfo(group="", version="v1", kind="Pod", plural="pods", namespaced=True)
        assert info.api_version == "v1"

    def test_api_version_grouped(self):
        info = APIResourceInfo(group="apps", version="v1", kind="Deployment",
                               plural="deployments", namespaced=True)
        assert info.api_version == "apps/v1"

    def test_is_listable_true(self):
        info = APIResourceInfo(group="", version="v1", kind="Pod", plural="pods",
                               namespaced=True, verbs=["list", "get"])
        assert info.is_listable

    def test_is_listable_false(self):
        info = APIResourceInfo(group="", version="v1", kind="Pod", plural="pods",
                               namespaced=True, verbs=["get"])
        assert not info.is_listable

    def test_str_grouped(self):
        info = APIResourceInfo(group="apps", version="v1", kind="Deployment",
                               plural="deployments", namespaced=True)
        assert str(info) == "apps/v1/Deployment"

    def test_str_core(self):
        info = APIResourceInfo(group="", version="v1", kind="Pod",
                               plural="pods", namespaced=True)
        assert str(info) == "v1/Pod"


class TestFlatten:
    def test_flat_dict(self):
        assert _flatten({"a": "1", "b": "2"}) == {"a": "1", "b": "2"}

    def test_nested_dict(self):
        assert _flatten({"a": {"b": "val"}}) == {"a.b": "val"}

    def test_list_value(self):
        result = _flatten({"items": [1, 2, 3]})
        assert result["items"] == "[3 items]"

    def test_none_value(self):
        assert _flatten({"key": None})["key"] == ""

    def test_max_depth_zero_returns_empty(self):
        assert _flatten({"a": "b"}, max_depth=0) == {}

    def test_max_depth_limits_recursion(self):
        obj = {"a": {"b": {"c": {"d": "deep"}}}}
        result = _flatten(obj, max_depth=2)
        assert "a.b.c.d" not in result

    def test_non_dict_root(self):
        result = _flatten("scalar")
        assert result == {"": "scalar"}

    def test_prefix_prepended(self):
        result = _flatten({"x": "y"}, prefix="root")
        assert result == {"root.x": "y"}

    def test_int_value_converted_to_str(self):
        result = _flatten({"count": 5})
        assert result["count"] == "5"


class TestGenericEntityFromApiObject:
    @staticmethod
    def _info(kind="MyResource", group="custom.io", version="v1alpha1"):
        return APIResourceInfo(
            group=group, version=version, kind=kind,
            plural=kind.lower() + "s", namespaced=True,
            verbs=["list", "get"],
        )

    def test_basic_fields(self):
        obj = {
            "metadata": {
                "uid": "uid-123",
                "name": "my-resource",
                "namespace": "default",
                "labels": {"app": "test"},
                "annotations": {},
            },
            "spec": {"replicas": 3},
            "status": {"ready": "True"},
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert entity.uid == "uid-123"
        assert entity.name == "my-resource"
        assert entity.namespace == "default"
        assert entity.labels == {"app": "test"}

    def test_uid_fallback_when_missing(self):
        obj = {"metadata": {"name": "res", "namespace": "ns"}}
        entity = GenericEntity.from_api_object(obj, self._info(kind="Foo"))
        assert "Foo" in entity.uid

    def test_spec_flattened(self):
        obj = {
            "metadata": {"uid": "u1", "name": "r"},
            "spec": {"replicas": 2, "nested": {"key": "val"}},
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert entity.spec_fields["replicas"] == "2"
        assert entity.spec_fields["nested.key"] == "val"

    def test_status_flattened(self):
        obj = {
            "metadata": {"uid": "u1", "name": "r"},
            "status": {"phase": "Running", "ready": "True"},
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert entity.status_fields["phase"] == "Running"

    def test_created_at_parsed(self):
        obj = {
            "metadata": {
                "uid": "u1",
                "name": "r",
                "creationTimestamp": "2024-01-15T10:30:00Z",
            }
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert entity.created_at is not None
        assert entity.created_at.year == 2024

    def test_created_at_invalid_is_none(self):
        obj = {
            "metadata": {"uid": "u1", "name": "r",
                         "creationTimestamp": "not-a-date"}
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert entity.created_at is None

    def test_to_text_contains_kind_and_namespace(self):
        obj = {"metadata": {"uid": "u1", "name": "myobj", "namespace": "prod"}}
        entity = GenericEntity.from_api_object(obj, self._info(kind="CronTab"))
        text = entity.to_text()
        assert "kind=CronTab" in text
        assert "namespace=prod" in text

    def test_to_text_spec_fields_present(self):
        obj = {
            "metadata": {"uid": "u1", "name": "r"},
            "spec": {"replicas": 5},
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert "spec.replicas=5" in entity.to_text()

    def test_to_text_api_version(self):
        obj = {"metadata": {"uid": "u1", "name": "r"}}
        entity = GenericEntity.from_api_object(
            obj, self._info(group="batch", version="v1", kind="CronJob")
        )
        assert "apiVersion=batch/v1" in entity.to_text()

    def test_labels_in_to_text(self):
        obj = {
            "metadata": {"uid": "u1", "name": "r",
                         "labels": {"env": "prod"}, "annotations": {}}
        }
        entity = GenericEntity.from_api_object(obj, self._info())
        assert "env=prod" in entity.to_text()

    def test_no_namespace_cluster_scoped(self):
        obj = {"metadata": {"uid": "u1", "name": "node-1"}}
        info = APIResourceInfo(group="", version="v1", kind="Node",
                               plural="nodes", namespaced=False)
        entity = GenericEntity.from_api_object(obj, info)
        assert entity.namespace is None
